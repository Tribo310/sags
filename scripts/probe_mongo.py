import asyncio
import os

from pymongo import AsyncMongoClient
from pymongo.operations import SearchIndexModel


async def main():
    c = AsyncMongoClient(os.environ["MONGO_URL"])
    db = c.probe
    await db.p.drop()
    await db.p.insert_many([
        {"name": "rice", "store": "a", "embedding": [1, 0, 0]},
        {"name": "beef", "store": "b", "embedding": [0, 1, 0]},
        {"name": "milk", "store": "a", "embedding": [0, 0, 1]},
    ])
    await db.p.create_search_index(SearchIndexModel(
        definition={"fields": [
            {"type": "vector", "path": "embedding", "numDimensions": 3, "similarity": "cosine"},
            {"type": "filter", "path": "store"},
        ]},
        name="vec", type="vectorSearch"))
    for _ in range(90):
        idx = await (await db.p.list_search_indexes("vec")).to_list()
        if idx and idx[0].get("queryable"):
            break
        await asyncio.sleep(1)
    r = await (await db.p.aggregate([
        {"$vectorSearch": {"index": "vec", "path": "embedding", "queryVector": [0.9, 0.1, 0],
                           "numCandidates": 10, "limit": 2, "filter": {"store": "a"}}},
        {"$project": {"_id": 0, "name": 1}},
    ])).to_list()
    print("vector:", r)

    async def txn(s):
        await db.p.update_one({"name": "rice"}, {"$set": {"x": 1}}, session=s)
    async with c.start_session() as s:
        await s.with_transaction(txn)
    print("transaction: ok")

    async def watch():
        async with await db.a.watch([{"$match": {"fullDocument.k": 1}}]) as st:
            async for ch in st:
                return ch["fullDocument"]["k"]
    t = asyncio.create_task(watch())
    await asyncio.sleep(1)
    await db.a.insert_one({"k": 1})
    print("change stream:", await asyncio.wait_for(t, 10))
    await c.drop_database("probe")


asyncio.run(main())
