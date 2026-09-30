import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


# Several planner processes can share one database (Atlas: teammates, tests). Each owns the plans it works on and
# renews a lease on them; only a plan whose lease went stale (its process died) is reset or rolled back. Resetting
# every running plan at startup, as before, stopped other processes' agents and refunded their live checkouts.
INSTANCE = uuid.uuid4().hex
LEASE_EVERY, LEASE_STALE = 30, timedelta(minutes=2)


def owned():
    return {"owner": INSTANCE, "lease": now()}


def stale():
    return {"$or": [{"lease": {"$lt": now() - LEASE_STALE}}, {"lease": {"$exists": False}}]}


async def reclaim():
    """Plans whose process died: agent loops marked stopped, checkouts rolled back."""
    await db.plans.update_many({"running": True, **stale()}, {"$set": {"running": False}})
    try:
        await recover()
    except Exception as e:
        print(f"checkout recovery failed, will retry: {e}")


async def keep_leases():
    while True:
        await asyncio.sleep(LEASE_EVERY)
        try:
            await db.plans.update_many({"owner": INSTANCE, "$or": [{"running": True}, {"state": "checking_out"}]},
                                       {"$set": {"lease": now()}})
            await reclaim()
        except Exception as e:
            print(f"lease renewal failed: {e!r}")


@asynccontextmanager
async def lifespan(app):
    print("store agents:", await stores.discover())
    await reclaim()
    task = asyncio.create_task(keep_leases())
    yield
    task.cancel()


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
        {"$set": {"running": True, **owned()}})


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
        await db.plans.insert_one({"_id": pid, "user_id": uid, "state": "planning", "version": 1, "running": True, **owned(),
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


@app.get("/plans")
async def list_plans(uid: Uid):
    """The caller's conversations, newest first, titled by their first message."""
    return await agg(db.plans, [
        {"$match": {"user_id": uid}},
        {"$sort": {"created": -1}},
        {"$limit": 50},
        {"$lookup": {"from": "audit_log", "localField": "_id", "foreignField": "plan_id", "as": "first", "pipeline": [
            {"$match": {"actor": "user", "action": "message"}}, {"$sort": {"ts": 1}}, {"$limit": 1}]}},
        {"$project": {"state": 1, "created": 1, "total": "$basket.total", "title": {"$first": "$first.args.text"}}},
    ])


@app.get("/plans/{pid}")
async def get_plan(pid: str, uid: Uid):
    return await own_plan(pid, uid, {"messages": 0, "quotes": 0})


async def plan_events(pid):
    """Replays the plan's audit log, then streams new entries live from a MongoDB change stream."""
    seen = set()
    async with await db.audit_log.watch([{"$match": {"operationType": "insert", "fullDocument.plan_id": pid}}]) as stream:
        async for e in db.audit_log.find({"plan_id": pid}).sort("ts", 1):
            seen.add(e["_id"])
            yield e
        async for ch in stream:
            if ch["fullDocument"]["_id"] not in seen:
                yield ch["fullDocument"]


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
    """A planner died mid-checkout (its lease went stale): undo whatever was reserved or paid and fail the plan."""
    async for plan in db.plans.find({"state": "checking_out", **stale()}):
        pid = plan["_id"]
        orders = [{"store": o["store"], "order_id": o["_id"]}
                  async for o in db.orders.find({"plan_id": pid, "status": {"$in": ["reserved", "placed"]}})]
        payments = [p["_id"] async for p in db.payments.find({"plan_id": pid, "status": {"$in": ["held", "captured"]}})]
        async with connect() as mcp:
            await undo(orders, payments, stores.call, Wallet(mcp), logger(pid, "recovery"))
        await transition(pid, "checking_out", "failed", error="planner restarted during checkout; payments refunded")


async def checkout_task(pid):
    plan = await transition(pid, "approved", "checking_out", **owned())
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
    await pickup_trip(pid, plan["user_id"], [r["store"] for r in receipts])


async def pickup_trip(pid, user_id, stores):
    """The drive to collect the orders: home -> every store that got one -> home. Nice-to-have, never fails checkout."""
    user = await db.users.find_one({"_id": user_id}, {"location": 1})
    lon, lat = user["location"]["coordinates"]
    trip = await mcp_tool("plan_trip", {"lon": lon, "lat": lat, "stores": stores})
    if "error" not in trip:  # saved before the audit event, which makes the UI refetch the plan
        await db.plans.update_one({"_id": pid}, {"$set": {"trip": trip}})
    await audit(pid, "system", "plan_trip", result={k: v for k, v in trip.items() if k != "geometry"})


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
    await transition(pid, plan["state"], "cancelled", trip=None)  # nothing to pick up any more
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


@app.get("/me/favorites")
async def my_favorites(uid: Uid):
    """Dishes the household told the agent it likes or won't eat (saved from the chat)."""
    return [{"id": str(f["_id"]), "dish": f.get("recipe") or f["dish"], "liked": f["liked"], "rule": f.get("tag") and f"no_{f['tag']}"}
            async for f in db.favorites.find({"user_id": uid}).sort("ts", -1)]


@app.delete("/me/favorites/{fid}")
async def forget_favorite(fid: str, uid: Uid):
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        r = await db.favorites.delete_one({"_id": ObjectId(fid), "user_id": uid})
    except InvalidId:
        raise HTTPException(404)
    if not r.deleted_count:
        raise HTTPException(404)
    return {"ok": True}


@app.get("/me/pantry")
async def pantry(uid: Uid):
    return await mcp_tool("get_pantry", {"user_id": uid})


@app.post("/me/pantry/tick")
async def tick(uid: Uid):
    """Demo: one day passes and the household eats its usual daily amounts (one pipeline update, no read-modify-write)."""
    r = await db.pantry.update_many({"user_id": uid, "daily_use": {"$gt": 0}}, [{"$set": {
        "amount": {"$max": [0, {"$subtract": ["$amount", "$daily_use"]}]}, "updated": "$$NOW"}}])
    return {"updated": r.modified_count}


async def pantry_alerts(uid):
    """Change stream on the pantry: a refill suggestion once an item lasts REFILL_DAYS or less."""
    async with await db.pantry.watch([{"$match": {"operationType": {"$in": ["insert", "update", "replace"]}, "fullDocument.user_id": uid}}],
                                     full_document="updateLookup") as stream:
        async for ch in stream:
            d = ch.get("fullDocument")
            if not d or d["user_id"] != uid or d.get("daily_use", 0) <= 0:
                continue
            days = round(d["amount"] / d["daily_use"], 1)  # rounded like get_pantry, so 0.7999/0.4 counts as 2 days
            if days <= REFILL_DAYS:
                yield {"key": d["key"], "name": d["name"], "unit": d["unit"], "amount": round(d["amount"], 2),
                       "days_left": days}


async def price_drops():
    """Change stream on product prices; the pre-image gives the old price. Drops of PRICE_DROP or more."""
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
            yield {"store": old["store"], "key": old["key"], "name": old["name_mn"], "old": old["price"], "new": new,
                   "pct": round(100 * (1 - new / old["price"])), "lowest_30d": bool(lows) and new <= lows[0]["min"]}


async def merged(sources):
    """Several live sources over one SSE connection, each message tagged with its source. Browsers allow 6 HTTP/1.1
    connections per host: three streams per tab hung the page after a refresh or a second tab."""
    q = asyncio.Queue()

    async def pump(name, gen):
        try:
            async for item in gen:
                await q.put(sse({**item, "stream": name}))
        except Exception as e:  # one broken change stream must not take the others down
            print(f"stream {name} stopped: {e!r}")

    tasks = [asyncio.create_task(pump(name, gen)) for name, gen in sources.items()]
    try:
        while True:
            yield await q.get()
    finally:
        for t in tasks:
            t.cancel()


@app.get("/me/stream")
async def live(uid: Uid, plan_id: str | None = None):
    """The one live connection a tab needs: the open plan's steps, pantry refill alerts and price drops."""
    sources = {"alert": pantry_alerts(uid), "price": price_drops()}
    if plan_id:
        await own_plan(plan_id, uid, {"_id": 1})
        sources["event"] = plan_events(plan_id)
    return StreamingResponse(merged(sources), media_type="text/event-stream")


class TtsIn(BaseModel):
    text: str = Field(min_length=1, max_length=800)  # tsuurAI's limit; the UI splits longer replies


TTS_VOICE = os.getenv("TTS_VOICE", "mbspeech")  # mbspeech (Khalkha) · baseline · mn1h
_spoken: dict[str, bytes] = {}  # text -> wav: a reply is often played again


@app.post("/tts")
async def tts(body: TtsIn, _: Uid):
    """Text to speech via tsuurAI (oyu API): POST {base}/audio/speech {text, voice} -> audio/wav."""
    base = os.getenv("OYUVOICE_URL", "").rstrip("/").removesuffix("/audio/transcriptions")
    if not base:
        raise HTTPException(503, "voice output is not configured (OYUVOICE_URL)")
    text = " ".join(body.text.replace("₮", " төгрөг").split())
    if text not in _spoken:
        async with httpx.AsyncClient(timeout=60) as h:
            r = await h.post(f"{base}/audio/speech", json={"text": text, "voice": TTS_VOICE},
                             headers={"Authorization": f"Bearer {os.getenv('OYUVOICE_API_KEY', '')}"})
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("audio"):
            raise HTTPException(502, f"voice service answered {r.status_code}: {r.text[:200]}")
        if len(_spoken) > 200:
            _spoken.clear()
        _spoken[text] = r.content
    return Response(_spoken[text], media_type="audio/wav")


@app.post("/stt")
async def stt(audio: UploadFile, _: Uid):
    """Speech to text via OyuVoice: OpenAI-style POST {base}/audio/transcriptions, multipart "file" -> {"text"}.
    Takes the browser's webm/opus recording as is."""
    base = os.getenv("OYUVOICE_URL", "").rstrip("/")
    if not base:
        raise HTTPException(503, "voice input is not configured (OYUVOICE_URL)")
    url = base if base.endswith("/audio/transcriptions") else f"{base}/audio/transcriptions"
    async with httpx.AsyncClient(timeout=60) as h:
        r = await h.post(url, headers={"Authorization": f"Bearer {os.getenv('OYUVOICE_API_KEY', '')}"},
                         files={"file": (audio.filename, await audio.read(), audio.content_type)})
    if r.status_code != 200:  # a clear message for the chat instead of a 500 traceback
        raise HTTPException(502, f"voice service answered {r.status_code}: {r.text[:200]}")
    return {"text": r.json().get("text", "")}
