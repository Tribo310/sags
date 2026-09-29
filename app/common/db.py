import os
from datetime import UTC, datetime

from pymongo import AsyncMongoClient

client = AsyncMongoClient(os.getenv("MONGO_URL", "mongodb://localhost:27017/?directConnection=true"), connect=False)
db = client[os.getenv("MONGO_DB", "shop")]


def now():
    return datetime.now(UTC)


async def audit(plan_id, actor, action, args=None, result=None, reason=None):
    await db.audit_log.insert_one({"ts": now(), "plan_id": plan_id, "actor": actor, "action": action,
                                   "args": args, "result": result, "reason": reason})
