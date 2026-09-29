from pymongo import ReturnDocument

from app.common.db import audit, db, now

ALLOWED = {
    "planning": {"awaiting_approval", "failed"},
    "awaiting_approval": {"planning", "approved", "cancelled"},
    "approved": {"checking_out", "cancelled"},
    "checking_out": {"completed", "failed"},
    "completed": {"cancelled"},
    "failed": {"planning"},
    "cancelled": set(),
}


def can(frm, to):
    return to in ALLOWED.get(frm, set())


async def transition(plan_id, frm, to, **fields):
    """Atomic compare-and-set on plan state; a concurrent change makes it fail instead of double-running."""
    if not can(frm, to):
        raise ValueError(f"illegal transition {frm} -> {to}")
    doc = await db.plans.find_one_and_update(
        {"_id": plan_id, "state": frm},
        {"$set": {"state": to, **fields}, "$push": {"history": {"state": to, "ts": now()}}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        raise ValueError(f"plan {plan_id} is not in state {frm}")
    await audit(plan_id, "system", "state", result={"from": frm, "to": to})
    return doc
