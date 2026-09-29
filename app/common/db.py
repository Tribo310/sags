import os
from datetime import UTC, datetime

from pymongo import AsyncMongoClient

client = AsyncMongoClient(os.getenv("MONGO_URL", "mongodb://localhost:27018/?directConnection=true"), connect=False)
db = client[os.getenv("MONGO_DB", "shop")]


def now():
    return datetime.now(UTC)


async def agg(coll, pipeline):
    return await (await coll.aggregate(pipeline)).to_list()


async def in_txn(fn):
    """Run fn(session) in a transaction (retried on transient errors); returns fn's result."""
    async with client.start_session() as s:
        return await s.with_transaction(fn)


async def audit(plan_id, actor, action, args=None, result=None, reason=None, model=None, meta=None):
    """meta: step trace for agents, {llm_ms, tool_ms, prompt_tokens, completion_tokens}."""
    await db.audit_log.insert_one({"ts": now(), "plan_id": plan_id, "actor": actor, "action": action,
                                   "args": args, "result": result, "reason": reason, "model": model, "meta": meta})
