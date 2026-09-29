import asyncio
import sys

from app.common.db import db


async def main(store, key):
    """Sell out an item at one store (checkout reroutes) or at 'all' stores (the agent replans)."""
    match = {"key": key} if store == "all" else {"store": store, "key": key}
    r = await db.products.update_many(match, {"$set": {"stock": 0}})
    print(f"{store}:{key} stock -> 0 (matched {r.matched_count})")


asyncio.run(main(sys.argv[1], sys.argv[2]))
