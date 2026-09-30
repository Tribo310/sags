import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.common.db import agg, audit, db, now
from app.common.state import StateConflict, reopen, transition
from app.agents.manager import run_manager
from app.agents.protocols import a2a as stores
from app.agents.protocols.mcp import call_json, connect
from app.planner.auth import current_user, router as auth_router
from app.planner.checkout import SoldOut, Wallet, checkout, undo

REFILL_DAYS = 2  # suggest a refill when a pantry item lasts this many days or less
PRICE_DROP = 0.10  # alert when a price falls by at least this fraction
Uid = Annotated[str, Depends(current_user)]


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
app.include_router(auth_router)
DIST = Path(__file__).parents[2] / "web" / "dist"  # built by `cd web && npm run build`
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.exception_handler(StateConflict)
async def conflict(_, e):
    return JSONResponse({"detail": str(e)}, status_code=409)


class ChatIn(BaseModel):
    text: str
    plan_id: str | None = None


class ApproveIn(BaseModel):
    version: int
    confirm_high: bool = False


@app.get("/")
async def index():
    if not (DIST / "index.html").exists():
        raise HTTPException(503, "frontend is not built: cd web && npm install && npm run build")
    return FileResponse(DIST / "index.html")


def sse(e):
    return f"data: {json.dumps({k: v for k, v in e.items() if k != '_id'}, default=str, ensure_ascii=False)}\n\n"


async def claim(pid):
    """One agent loop per plan: a second message while it works would fork the conversation. A plan awaiting
    approval stays proposed while the agent answers; it's reopened only if the agent changes it (manager.replan)."""
    return await db.plans.find_one_and_update(
        {"_id": pid, "state": {"$in": ["planning", "awaiting_approval"]}, "running": {"$ne": True}},
        {"$set": {"running": True}})


async def own_plan(pid, uid, projection=None):
    """The caller's plan; someone else's plan is a 404, same as a missing one."""
    plan = await db.plans.find_one({"_id": pid, "user_id": uid}, projection)
    if not plan:
        raise HTTPException(404)
    return plan


@app.post("/chat")
async def chat(body: ChatIn, bg: BackgroundTasks, uid: Uid):
    if body.plan_id:
        plan = await own_plan(body.plan_id, uid)
        pid = plan["_id"]
        if plan["state"] == "failed":
            await reopen(plan)
        if not await claim(pid):
            raise HTTPException(409, "the agent is still working on this plan, or it can't be changed any more")
    else:
        pid = uuid.uuid4().hex
        await db.plans.insert_one({"_id": pid, "user_id": uid, "state": "planning", "version": 1, "running": True,
                                   "created": now(), "history": [{"state": "planning", "ts": now()}]})
    await audit(pid, "user", "message", args={"text": body.text})
    bg.add_task(agent_task, pid, body.text)
    return {"plan_id": pid}


async def agent_task(pid, text):
    try:
        await run_manager(pid, text)
    except Exception as e:
        await audit(pid, "agent", "error", result={"error": f"{type(e).__name__}: {e}"})
        if (await db.plans.find_one({"_id": pid}))["state"] == "planning":
            await transition(pid, "planning", "failed", error=str(e))
    finally:
        await db.plans.update_one({"_id": pid}, {"$set": {"running": False}})


@app.get("/plans/{pid}")
async def get_plan(pid: str, uid: Uid):
    return await own_plan(pid, uid, {"messages": 0, "quotes": 0})


@app.get("/plans/{pid}/events")
async def events(pid: str, uid: Uid):
    """Replays the plan's audit log, then streams new entries live from a MongoDB change stream."""
    await own_plan(pid, uid, {"_id": 1})
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
async def approve(pid: str, body: ApproveIn, bg: BackgroundTasks, uid: Uid):
    plan = await own_plan(pid, uid)
    if plan["state"] != "awaiting_approval" or plan["version"] != body.version:
        raise HTTPException(409, "plan changed or is not awaiting approval")
    if plan.get("running"):  # the agent may be about to change it
        raise HTTPException(409, "the assistant is still answering; approve once it's done")
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
        plan = await transition(pid, "checking_out", "failed", error=str(e))
        if isinstance(e, SoldOut):  # checkout already rolled back; let the agent fix the plan itself
            await reopen(plan)
            if await claim(pid):
                await audit(pid, "system", "replan", result={"sold_out": e.name})
                await agent_task(pid, f"[system] Checkout stopped: {e.name} is sold out at every store. Keep the rest of "
                                      "the plan, replace that item or the recipes that use it, quote again and propose "
                                      "the new plan. Reply in the user's language.")
        return
    await transition(pid, "checking_out", "completed", receipts=receipts)
    await stock_pantry(plan["user_id"], receipts, 1)


async def stock_pantry(user_id, receipts, sign):
    """Bought groceries go into the pantry (sign=1) so the next list skips them; a refund takes them out (sign=-1)."""
    lines = [l for r in receipts for l in r["lines"]]
    packs = {p["key"]: p async for p in db.products.find({"key": {"$in": [l["key"] for l in lines]}}, {"key": 1, "pack": 1, "unit": 1})}
    for l in lines:
        p = packs[l["key"]]
        await db.pantry.update_one({"user_id": user_id, "key": l["key"]}, [{"$set": {
            "amount": {"$max": [0, {"$add": [{"$ifNull": ["$amount", 0]}, sign * l["qty"] * p["pack"]]}]},
            "daily_use": {"$ifNull": ["$daily_use", 0]}, "name": {"$literal": l["name"]}, "unit": p["unit"], "updated": "$$NOW"}}],
            upsert=True)


@app.post("/plans/{pid}/cancel")
async def cancel(pid: str, uid: Uid):
    plan = await own_plan(pid, uid)
    await audit(pid, "user", "cancel")
    if plan["state"] == "completed":  # already paid: refund and restock
        receipts = plan.get("receipts", [])
        async with connect() as mcp:
            await undo(receipts, [r["payment_id"] for r in receipts], stores.call, Wallet(mcp), logger(pid, "cancel"))
        await stock_pantry(plan["user_id"], receipts, -1)
    await transition(pid, plan["state"], "cancelled")
    return {"ok": True}


async def mcp_tool(name, args):
    async with connect() as mcp:
        return await call_json(mcp, name, args)


@app.get("/metrics/agents")
async def agent_metrics(_: Uid, hours: int = 24):
    """Per-agent health from the ReAct traces in audit_log: steps, tool errors, stalls, latency, tokens, models."""
    return await agg(db.audit_log, [
        {"$match": {"meta": {"$ne": None}, "ts": {"$gte": now() - timedelta(hours=hours)}}},
        {"$group": {"_id": "$actor", "steps": {"$sum": 1},
                    "tool_errors": {"$sum": {"$cond": [{"$ifNull": ["$result.error", False]}, 1, 0]}},
                    "stalls": {"$sum": {"$ifNull": ["$meta.stalls", 0]}},
                    "llm_ms": {"$percentile": {"input": "$meta.llm_ms", "p": [0.5, 0.95], "method": "approximate"}},
                    "tool_ms": {"$percentile": {"input": "$meta.tool_ms", "p": [0.5, 0.95], "method": "approximate"}},
                    "tokens": {"$sum": {"$add": [{"$ifNull": ["$meta.prompt_tokens", 0]},
                                                 {"$ifNull": ["$meta.completion_tokens", 0]}]}},
                    "models": {"$addToSet": "$model"}}},
        {"$set": {"agent": "$_id", "error_rate": {"$round": [{"$divide": ["$tool_errors", "$steps"]}, 3]}}},
        {"$project": {"_id": 0}}, {"$sort": {"steps": -1}}])


@app.get("/me/stats")
async def stats(uid: Uid):
    return await mcp_tool("spending_stats", {"user_id": uid})


@app.get("/me/pantry")
async def pantry(uid: Uid):
    return await mcp_tool("get_pantry", {"user_id": uid})


@app.post("/me/pantry/tick")
async def tick(uid: Uid):
    """Demo: one day passes and the household eats its usual daily amounts (one pipeline update, no read-modify-write)."""
    r = await db.pantry.update_many({"user_id": uid, "daily_use": {"$gt": 0}}, [{"$set": {
        "amount": {"$max": [0, {"$subtract": ["$amount", "$daily_use"]}]}, "updated": "$$NOW"}}])
    return {"updated": r.modified_count}


@app.get("/me/alerts")
async def alerts(uid: Uid):
    """Change stream on the pantry: pushes a refill suggestion once an item lasts REFILL_DAYS or less."""
    async def gen():
        async with await db.pantry.watch([{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}, "fullDocument.user_id": uid}}],
                                         full_document="updateLookup") as stream:
            async for ch in stream:
                d = ch.get("fullDocument")
                if not d or d["user_id"] != uid or d.get("daily_use", 0) <= 0:
                    continue
                days = round(d["amount"] / d["daily_use"], 1)  # rounded like get_pantry, so 0.7999/0.4 counts as 2 days
                if days <= REFILL_DAYS:
                    yield sse({"key": d["key"], "name": d["name"], "unit": d["unit"], "amount": round(d["amount"], 2),
                               "days_left": days})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/price-alerts")
async def price_alerts():
    """Change stream on product prices; the pre-image gives the old price. Pushes drops of PRICE_DROP or more."""
    async def gen():
        async with await db.products.watch(
                [{"$match": {"operationType": "update", "updateDescription.updatedFields.price": {"$exists": True}}}],
                full_document_before_change="whenAvailable") as stream:
            async for ch in stream:
                old, new = ch.get("fullDocumentBeforeChange"), ch["updateDescription"]["updatedFields"]["price"]
                if not old or new > old["price"] * (1 - PRICE_DROP):
                    continue
                lows = await agg(db.price_history, [{"$match": {"meta.key": old["key"], "meta.store": old["store"],
                                                                 "ts": {"$gte": now() - timedelta(days=30)}}},
                                                     {"$group": {"_id": None, "min": {"$min": "$price"}}}])
                yield sse({"store": old["store"], "key": old["key"], "name": old["name_mn"], "old": old["price"], "new": new,
                           "pct": round(100 * (1 - new / old["price"])),
                           "lowest_30d": bool(lows) and new <= lows[0]["min"]})
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/stt")
async def stt(audio: UploadFile, _: Uid):
    # ADJUST to the OyuVoice API (Task 1 Step 3): endpoint, auth header, field name, response path.
    if not os.getenv("OYUVOICE_URL"):
        raise HTTPException(503, "voice input is not configured (OYUVOICE_URL)")
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(os.environ["OYUVOICE_URL"],
                         headers={"Authorization": f"Bearer {os.getenv('OYUVOICE_API_KEY', '')}"},
                         files={"file": (audio.filename, await audio.read(), audio.content_type)})
    r.raise_for_status()
    return {"text": r.json().get("text", "")}
