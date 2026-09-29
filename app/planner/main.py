import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from mcp import Client

from app.common.db import audit, db, now
from app.common.state import transition
from app.planner import stores
from app.planner.agent import MCP_URL, mcp_json, run_agent
from app.planner.checkout import CheckoutError, checkout


@asynccontextmanager
async def lifespan(app):
    print("store agents:", await stores.discover())
    yield


app = FastAPI(lifespan=lifespan)
WEB = Path(__file__).parent.parent / "web" / "index.html"


@app.exception_handler(ValueError)  # illegal/raced state transitions
async def conflict(_, e):
    return JSONResponse({"detail": str(e)}, status_code=409)


class ChatIn(BaseModel):
    text: str
    user_id: str = "demo"
    plan_id: str | None = None


class ApproveIn(BaseModel):
    version: int
    confirm_high: bool = False


class Wallet:
    def __init__(self, mcp):
        self.mcp = mcp

    async def _call(self, name, **args):
        d = await mcp_json(self.mcp, name, args)
        if not d.get("ok"):
            raise CheckoutError(f"{name}: {d.get('violations') or d.get('error')}")
        return d

    async def hold(self, plan_id, store, amount):
        return (await self._call("wallet_hold", plan_id=plan_id, store=store, amount=amount))["payment_id"]

    async def capture(self, payment_id):
        await self._call("wallet_capture", payment_id=payment_id)

    async def refund(self, payment_id):
        await self._call("wallet_refund", payment_id=payment_id)


@app.get("/")
async def index():
    return FileResponse(WEB)


@app.post("/chat")
async def chat(body: ChatIn, bg: BackgroundTasks):
    if body.plan_id:
        plan = await db.plans.find_one({"_id": body.plan_id})
        if not plan:
            raise HTTPException(404)
        if plan["state"] in ("awaiting_approval", "failed"):  # user changes the plan -> new version, old approval void
            await transition(plan["_id"], plan["state"], "planning", version=plan["version"] + 1)
        elif plan["state"] != "planning":
            raise HTTPException(409, f"plan is {plan['state']}")
        pid = plan["_id"]
    else:
        pid = uuid.uuid4().hex
        await db.plans.insert_one({"_id": pid, "user_id": body.user_id, "state": "planning", "version": 1,
                                   "created": now(), "history": [{"state": "planning", "ts": now()}]})
    await audit(pid, "user", "message", args={"text": body.text})
    bg.add_task(agent_task, pid, body.text)
    return {"plan_id": pid}


async def agent_task(pid, text):
    try:
        await run_agent(pid, text)
    except Exception as e:
        await audit(pid, "agent", "error", result={"error": f"{type(e).__name__}: {e}"})
        if (await db.plans.find_one({"_id": pid}))["state"] == "planning":
            await transition(pid, "planning", "failed", error=str(e))


@app.get("/plans/{pid}")
async def get_plan(pid: str):
    plan = await db.plans.find_one({"_id": pid}, {"messages": 0, "quotes": 0})
    if not plan:
        raise HTTPException(404)
    return plan


@app.get("/plans/{pid}/events")
async def events(pid: str):
    """Replays the plan's audit log, then streams new entries live from a MongoDB change stream."""
    def sse(e):
        return f"data: {json.dumps({k: v for k, v in e.items() if k != '_id'}, default=str, ensure_ascii=False)}\n\n"

    async def gen():
        seen = set()
        async with await db.audit_log.watch([{"$match": {"operationType": "insert", "fullDocument.plan_id": pid}}]) as stream:
            async for e in db.audit_log.find({"plan_id": pid}).sort("ts", 1):
                seen.add(e["_id"])
                yield sse(e)
            async for ch in stream:
                if ch["fullDocument"]["_id"] not in seen:
                    yield sse(ch["fullDocument"])
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/plans/{pid}/approve")
async def approve(pid: str, body: ApproveIn, bg: BackgroundTasks):
    plan = await db.plans.find_one({"_id": pid})
    if not plan or plan["state"] != "awaiting_approval" or plan["version"] != body.version:
        raise HTTPException(409, "plan changed or is not awaiting approval")
    user = await db.users.find_one({"_id": plan["user_id"]})
    total = plan["basket"]["total"]
    if total > user["policy"]["approval_threshold"] and not body.confirm_high:
        return {"needs_second_confirm": True, "total": total}
    await audit(pid, "user", "approve", args={"version": body.version, "total": total})
    await transition(pid, "awaiting_approval", "approved", approved_version=plan["version"], approved_total=total)
    bg.add_task(checkout_task, pid)
    return {"ok": True}


async def checkout_task(pid):
    plan = await transition(pid, "approved", "checking_out")

    async def log(action, **kw):
        await audit(pid, "checkout", action, result=kw)
    try:
        async with Client(MCP_URL) as mcp:
            receipts = await checkout(plan, stores.call, Wallet(mcp), log)
    except Exception as e:
        await transition(pid, "checking_out", "failed", error=str(e))
        return
    await transition(pid, "checking_out", "completed", receipts=receipts)


@app.post("/plans/{pid}/cancel")
async def cancel(pid: str):
    plan = await db.plans.find_one({"_id": pid})
    if not plan:
        raise HTTPException(404)
    if plan["state"] == "completed":
        async with Client(MCP_URL) as mcp:
            wallet = Wallet(mcp)
            for r in plan.get("receipts", []):
                await stores.call(r["store"], {"skill": "cancel", "order_id": r["order_id"]})
                await wallet.refund(r["payment_id"])
    await audit(pid, "user", "cancel")
    await transition(pid, plan["state"], "cancelled")
    return {"ok": True}


@app.post("/stt")
async def stt(audio: UploadFile):
    # ADJUST to the OyuVoice API (Task 1 Step 3): endpoint, auth header, field name, response path.
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(os.environ["OYUVOICE_URL"],
                         headers={"Authorization": f"Bearer {os.getenv('OYUVOICE_API_KEY', '')}"},
                         files={"file": (audio.filename, await audio.read(), audio.content_type)})
    r.raise_for_status()
    return {"text": r.json().get("text", "")}
