import asyncio
import hashlib
import json
import pathlib

from pymongo.operations import SearchIndexModel

from app.common.db import db
from app.common.embed import DIMS, embed
from app.common.policy import DEFAULT_POLICY

D = pathlib.Path(__file__).parent
STORES = {"nomin": 1.04, "emart": 0.98, "sansar": 1.0}  # store price level
USER = {"_id": "demo", "name": "Demo household", "household": 2, "diet": ["no_pork"],
        "location": {"type": "Point", "coordinates": [106.9177, 47.9186]},  # Sukhbaatar square
        "wallet": 500000, "policy": DEFAULT_POLICY}
# What the demo household has at home: key -> (amount in the product's unit, daily use).
PANTRY = {"rice": (1.5, 0.1), "flour": (2, 0.15), "milk": (1.2, 0.4), "eggs": (6, 2), "oil": (0.5, 0.03),
          "salt": (0.8, 0.01), "tea": (0.2, 0.02)}
USER_SCHEMA = {"bsonType": "object", "required": ["household", "wallet", "location", "policy"], "properties": {
    "household": {"bsonType": "int", "minimum": 1},
    "wallet": {"bsonType": ["int", "long"], "minimum": 0},  # DB refuses a negative balance
    "policy": {"bsonType": "object", "required": ["budget", "approval_threshold", "max_stores", "allowed_stores"],
               "properties": {"budget": {"bsonType": ["int", "long"], "minimum": 0},
                              "max_stores": {"bsonType": "int", "minimum": 1}}}}}


def jitter(seed, lo, hi):
    """Deterministic pseudo-random number in [lo, hi) so every reseed gives the same prices."""
    return lo + (hi - lo) * (int(hashlib.md5(seed.encode()).hexdigest(), 16) % 1000 / 1000)


def store_price(base, key, store):
    return round(base * STORES[store] * jitter(f"{key}:{store}", 0.88, 1.12) / 10) * 10


async def vector_index(coll, name, filters):
    await coll.create_search_index(SearchIndexModel(name=name, type="vectorSearch", definition={"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": DIMS, "similarity": "cosine"},
        *({"type": "filter", "path": f} for f in filters)]}))
    for _ in range(120):
        idx = await (await coll.list_search_indexes(name)).to_list()
        if idx and idx[0].get("queryable"):
            return
        await asyncio.sleep(1)
    raise RuntimeError(f"search index {name} never became queryable")


async def main():
    items = json.loads((D / "items.json").read_text())
    recipes = json.loads((D / "recipes.json").read_text())
    branches = json.loads((D / "branches.json").read_text())

    for c in ("users", "products", "recipes", "branches", "orders", "payments", "plans", "audit_log", "agent_memory", "pantry"):
        await db.drop_collection(c)

    await db.create_collection("users", validator={"$jsonSchema": USER_SCHEMA})
    await db.users.insert_one(USER)

    vecs = await embed([f"{i['name_mn']} {i['name_en']} {i['category']}" for i in items])
    await db.products.insert_many([
        {**i, "_id": f"{s}:{i['key']}", "store": s, "price": store_price(i["price"], i["key"], s),
         "stock": int(jitter(f"{i['key']}:{s}:stock", 5, 60)), "embedding": v}
        for s in STORES for i, v in zip(items, vecs)])

    vecs = await embed([f"{r['name_mn']} {r['name_en']} {' '.join(r['tags'])} "
                        f"{' '.join(g['key'] for g in r['ingredients'])}" for r in recipes])
    await db.recipes.insert_many([{**{k: v for k, v in r.items() if k != "id"}, "_id": r["id"], "embedding": e}
                                  for r, e in zip(recipes, vecs)])

    await db.branches.insert_many([{**{k: v for k, v in b.items() if k != "lonlat"},
                                    "location": {"type": "Point", "coordinates": b["lonlat"]}} for b in branches])

    by_key = {i["key"]: i for i in items}
    await db.pantry.insert_many([{"user_id": USER["_id"], "key": k, "name": by_key[k]["name_mn"], "unit": by_key[k]["unit"],
                                  "amount": amount, "daily_use": use} for k, (amount, use) in PANTRY.items()])

    await db.pantry.create_index([("user_id", 1), ("key", 1)], unique=True)
    await db.products.create_index([("store", 1), ("key", 1)], unique=True)
    await db.products.create_index([("key", 1), ("price", 1)])
    await db.branches.create_index([("location", "2dsphere")])
    await db.orders.create_index("idempotency_key", unique=True)
    await db.payments.create_index("plan_id")
    await db.audit_log.create_index([("plan_id", 1), ("ts", 1)])
    await db.agent_memory.create_index([("user_id", 1), ("ts", -1)])
    await vector_index(db.products, "products_vec", ["store"])
    await vector_index(db.recipes, "recipes_vec", ["tags"])
    print(f"seeded {len(items) * len(STORES)} products, {len(recipes)} recipes, {len(branches)} branches")


if __name__ == "__main__":
    asyncio.run(main())
