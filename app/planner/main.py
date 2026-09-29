import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.common.db import audit, db, now
from app.common.state import StateConflict, transition
from app.planner import stores
from app.planner.agent import run_agent
from app.planner.checkout import checkout, undo
from app.planner.mcp_client import Wallet, connect


@asynccontextmanager
async def lifespan(app):
    print("store agents:", await stores.discover())
    await db.plans.update_many({"running": True}, {"$set": {"running": False}})  # loops died with the old process
    try:
        await recover()
    except Exception as e:
        print(f"checkout recovery failed, will retry on next start: {e}")
    yield


app = FastAPI(lifespan=lifespan)
WEB = Path(__file__).parent.parent / "web" / "index.html"


@app.exception_handler(StateConflict)
async def conflict(_, e):
    return JSONResponse({"detail": str(e)}, status_code=409)


class ChatIn(BaseModel):
    text: str
    user_id: str = "demo"
    plan_id: str | None = None


class ApproveIn(BaseModel):
    version: int
    confirm_high: bool = False


@app.get("/")
async def index():
    return FileResponse(WEB)


@app.post("/chat")
async def chat(body: ChatIn, bg: BackgroundTasks):
    if body.plan_id:
        plan = await db.plans.find_one({"_id": body.plan_id})
        if not plan:
            raise HTTPException(404)
        pid = plan["_id"]
        if plan["state"] in ("awaiting_approval", "failed"):  # user changes the plan -> new version, old approval void
            await transition(pid, plan["state"], "planning", version=plan["version"] + 1,
                             basket=None, quotes=None, summary=None, branches=None, error=None)
        # One agent loop per plan: a second message while it works would fork the conversation.
        if not await db.plans.find_one_and_update({"_id": pid, "state": "planning", "running": {"$ne": True}},
                                                  {"$set": {"running": True}}):
            raise HTTPException(409, "the agent is still working on this plan, or it can't be changed any more")
    else:
        pid = uuid.uuid4().hex
        await db.plans.insert_one({"_id": pid, "user_id": body.user_id, "state": "planning", "version": 1, "running": True,
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
    finally:
        await db.plans.update_one({"_id": pid}, {"$set": {"running": False}})


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
    await transition(pid, "awaiting_approval", "approved", match={"version": body.version},
                     approved_version=body.version, approved_total=total)
    bg.add_task(checkout_task, pid)
    return {"ok": True}


def logger(pid, actor):
    async def log(action, **kw):
        await audit(pid, actor, action, result=kw)
    return log


async def recover():
    """The planner died mid-checkout: undo whatever was reserved or paid and fail the plan."""
    async for plan in db.plans.find({"state": "checking_out"}):
        pid = plan["_id"]
        orders = [{"store": o["store"], "order_id": o["_id"]}
                  async for o in db.orders.find({"plan_id": pid, "status": {"$in": ["reserved", "placed"]}})]
        payments = [p["_id"] async for p in db.payments.find({"plan_id": pid, "status": {"$in": ["held", "captured"]}})]
        async with connect() as mcp:
            await undo(orders, payments, stores.call, Wallet(mcp), logger(pid, "recovery"))
        await transition(pid, "checking_out", "failed", error="planner restarted during checkout; payments refunded")


async def checkout_task(pid):
    plan = await transition(pid, "approved", "checking_out")
    try:
        async with connect() as mcp:
            receipts = await checkout(plan, stores.call, Wallet(mcp), logger(pid, "checkout"))
    except Exception as e:
        await transition(pid, "checking_out", "failed", error=str(e))
        return
    await transition(pid, "checking_out", "completed", receipts=receipts)


@app.post("/plans/{pid}/cancel")
async def cancel(pid: str):
    plan = await db.plans.find_one({"_id": pid})
    if not plan:
        raise HTTPException(404)
    await audit(pid, "user", "cancel")
    if plan["state"] == "completed":  # already paid: refund and restock
        receipts = plan.get("receipts", [])
        async with connect() as mcp:
            await undo(receipts, [r["payment_id"] for r in receipts], stores.call, Wallet(mcp), logger(pid, "cancel"))
    await transition(pid, plan["state"], "cancelled")
    return {"ok": True}


@app.post("/stt")
async def stt(audio: UploadFile):
    # ADJUST to the OyuVoice API (Task 1 Step 3): endpoint, auth header, field name, response path.
    if not os.getenv("OYUVOICE_URL"):
        raise HTTPException(503, "voice input is not configured (OYUVOICE_URL)")
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(os.environ["OYUVOICE_URL"],
                         headers={"Authorization": f"Bearer {os.getenv('OYUVOICE_API_KEY', '')}"},
                         files={"file": (audio.filename, await audio.read(), audio.content_type)})
    r.raise_for_status()
    return {"text": r.json().get("text", "")}
