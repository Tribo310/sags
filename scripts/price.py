import asyncio
import sys

from app.common.db import db, now


async def main(store, key, price):
    """Demo: a store changes a price. Recorded in the price history; the planner's change stream alerts on drops."""
    p = await db.products.find_one_and_update({"store": store, "key": key}, {"$set": {"price": price}})
    if not p:
        sys.exit(f"unknown product {store}:{key}")
    await db.price_history.insert_one({"ts": now(), "meta": {"store": store, "key": key}, "price": price})
    print(f"{store}:{key} {p['price']} -> {price}")


asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
