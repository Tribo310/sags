import asyncio
import sys

from app.common.db import db


async def main(store, key):
    r = await db.products.update_one({"store": store, "key": key}, {"$set": {"stock": 0}})
    print(f"{store}:{key} stock -> 0 (matched {r.matched_count})")


asyncio.run(main(sys.argv[1], sys.argv[2]))
