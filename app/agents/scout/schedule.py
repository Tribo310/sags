"""The price scout on its own: once a day at SCOUT_HOUR (Ulaanbaatar time), whichever planner process gets there first.

Every planner runs this loop; a lock document in MongoDB makes sure only one of them crawls, and the last recorded
run makes sure it's once a day even across restarts. A day missed while everything was down is caught up by the
next planner that starts after SCOUT_HOUR."""
import asyncio
import os
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from app.agents.scout.agent import load_items, scout
from app.common.db import db, now

UB = ZoneInfo("Asia/Ulaanbaatar")
HOUR = int(os.getenv("SCOUT_HOUR", "3"))  # local hour of the daily run: prices are stable overnight, sites are quiet
CHECK_EVERY = 600  # seconds between "is it due?" checks
LOCK_FOR = timedelta(minutes=30)  # a run takes 5-8 minutes; a process that dies mid-run frees the lock by then
ME = uuid.uuid4().hex


def due_since(at=None):
    """The most recent scheduled moment (today at HOUR, or yesterday's if it's earlier than HOUR now), in UTC."""
    local = (at or now()).astimezone(UB)
    slot = datetime.combine(local.date(), time(HOUR), UB)
    return (slot if local >= slot else slot - timedelta(days=1)).astimezone(now().tzinfo)


async def last_run():
    return await db.scout_runs.find_one({"ok": True}, sort=[("ts", -1)])


async def is_due():
    last = await last_run()
    return not last or last["ts"].replace(tzinfo=now().tzinfo) < due_since()


async def take_lock():
    """True for exactly one process. The filter matches a missing or expired lock; a held one doesn't match, so the
    upsert tries to insert a second "scout" document and fails on the _id."""
    try:
        await db.locks.find_one_and_update(
            {"_id": "scout", "until": {"$lt": now()}}, {"$set": {"owner": ME, "until": now() + LOCK_FOR}}, upsert=True)
    except DuplicateKeyError:
        return False
    return True


async def release():
    await db.locks.update_one({"_id": "scout", "owner": ME}, {"$set": {"until": now()}})


async def run_once(log=print):
    started = now()
    try:
        summary, _ = await scout(load_items(), crawl=True, write=True, log=lambda m: None)
        await db.scout_runs.insert_one({"ts": started, "finished": now(), "ok": True, "summary": summary})
        log(f"price scout: {summary}")
    except Exception as e:
        await db.scout_runs.insert_one({"ts": started, "finished": now(), "ok": False, "error": repr(e)[:500]})
        log(f"price scout failed: {e!r}")


async def daily(log=print):
    while True:
        try:
            if await is_due() and await take_lock():
                try:
                    if await is_due():  # another process may have finished a run while we waited for the lock
                        await run_once(log)
                finally:
                    await release()
        except Exception as e:
            log(f"price scout schedule: {e!r}")
        await asyncio.sleep(CHECK_EVERY)


async def status():
    """What the UI shows: when prices were last refreshed and when the next refresh is."""
    last = await last_run()
    nxt = due_since() + timedelta(days=1)
    return {"last": last and last["ts"].replace(tzinfo=now().tzinfo), "next": nxt, "hour": HOUR}
