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


class StateConflict(Exception):
    """Illegal transition, or the plan changed under us (another request won the race)."""


def can(frm, to):
    return to in ALLOWED.get(frm, set())


async def transition(plan_id, frm, to, match=None, **fields):
    """Atomic compare-and-set on plan state (plus any extra `match` filter, e.g. version)."""
    if not can(frm, to):
        raise StateConflict(f"illegal transition {frm} -> {to}")
    doc = await db.plans.find_one_and_update(
        {"_id": plan_id, "state": frm, **(match or {})},
        {"$set": {"state": to, **fields}, "$push": {"history": {"state": to, "ts": now()}}},
        return_document=ReturnDocument.AFTER)
    if doc is None:
        raise StateConflict(f"plan {plan_id} is no longer {frm} (changed concurrently)")
    await audit(plan_id, "system", "state", result={"from": frm, "to": to})
    return doc


async def reopen(plan):
    """The plan changes (the agent replans, or a sold-out checkout): new version, the old approval is void."""
    return await transition(plan["_id"], plan["state"], "planning", match={"version": plan["version"]},
                            version=plan["version"] + 1, basket=None, quotes=None, needed=None, offers=None,
                            negotiated=None, summary=None, branches=None, error=None)
