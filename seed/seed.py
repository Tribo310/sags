import asyncio
import hashlib
import json
import os
import pathlib
from datetime import timedelta

from pymongo.errors import OperationFailure
from pymongo.operations import SearchIndexModel

from app.common.db import db, now
from app.common.embed import DIMS, embed
from app.common.policy import DEFAULT_POLICY
from app.planner.auth import hash_password

D = pathlib.Path(__file__).parent
STORES = {"nomin": 1.04, "emart": 0.98, "sansar": 1.0}  # store price level
USER = {"_id": "demo", "name": "Demo household", "email": "demo@demo.mn", "password": hash_password("demo1234"),
        "household": 2, "diet": ["no_pork"],
        "location": {"type": "Point", "coordinates": [106.9177, 47.9186]},  # Sukhbaatar square
        "wallet": 500000, "policy": DEFAULT_POLICY}
# What the demo household has at home: key -> (amount in the product's unit, daily use).
PANTRY = {"rice": (1.5, 0.1), "flour": (2, 0.15), "milk": (1.2, 0.4), "eggs": (6, 2), "oil": (0.5, 0.03),
          "salt": (0.8, 0.01), "tea": (0.2, 0.02)}
HISTORY_DAYS = 30
RESERVE_TTL = int(os.getenv("RESERVE_TTL", "900"))  # unpaid reservations expire after this many seconds
USER_SCHEMA = {"bsonType": "object", "required": ["email", "password", "household", "wallet", "location", "policy"], "properties": {
    "email": {"bsonType": "string"}, "password": {"bsonType": "string"},
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
    await search_index(coll, name, "vectorSearch", {"fields": [
        {"type": "vector", "path": "embedding", "numDimensions": DIMS, "similarity": "cosine"},
        *({"type": "filter", "path": f} for f in filters)]})


async def search_index(coll, name, type, definition):
    await coll.create_search_index(SearchIndexModel(name=name, type=type, definition=definition))
    for _ in range(120):
        idx = await (await coll.list_search_indexes(name)).to_list()
        if idx and idx[0].get("queryable"):
            return
        await asyncio.sleep(1)
    raise RuntimeError(f"search index {name} never became queryable")


async def reset_household():
    """The demo household back to its seeded pantry, memory, favourites, wallet and policy (evals start from here)."""
    by_key = {i["key"]: i for i in json.loads((D / "items.json").read_text())}
    await db.pantry.delete_many({"user_id": USER["_id"]})
    await db.pantry.insert_many([{"user_id": USER["_id"], "key": k, "name": by_key[k]["name_mn"], "unit": by_key[k]["unit"],
                                  "amount": amount, "daily_use": use} for k, (amount, use) in PANTRY.items()])
    await db.agent_memory.delete_many({"user_id": USER["_id"]})
    await db.favorites.delete_many({"user_id": USER["_id"]})
    await db.users.update_one({"_id": USER["_id"]}, {"$set": {"wallet": USER["wallet"], "policy": USER["policy"]}})


CATALOG = ("products", "recipes", "branches", "price_history", "offers")
PEOPLE = ("users", "orders", "payments", "plans", "audit_log", "agent_memory", "pantry", "favorites")


async def main(catalog_only=False):
    """Everything from scratch, or with catalog_only just the catalog: a shared database (Atlas) keeps its users,
    plans and history, and the demo household is added if it's missing."""
    items = json.loads((D / "items.json").read_text())
    recipes = json.loads((D / "recipes.json").read_text())
    branches = json.loads((D / "branches.json").read_text())

    for c in CATALOG if catalog_only else CATALOG + PEOPLE:
        await db.drop_collection(c)

    existing = await db.list_collection_names()
    if "users" not in existing:
        await db.create_collection("users", validator={"$jsonSchema": USER_SCHEMA})
    await db.users.create_index("email", unique=True)
    if not await db.users.find_one({"_id": USER["_id"]}):
        await db.users.insert_one(USER)

    # Pre-images let change streams see the old price (price alerts) and deleted orders (expired reservations).
    for c in ("products", "orders"):
        if c not in existing or c in CATALOG:
            await db.create_collection(c, changeStreamPreAndPostImages={"enabled": True})
    await db.create_collection("price_history", timeseries={"timeField": "ts", "metaField": "meta", "granularity": "hours"})

    vecs = await embed([f"{i['name_mn']} {i['name_en']} {i['category']}" for i in items])
    await db.products.insert_many([
        {**i, "_id": f"{s}:{i['key']}", "store": s, "price": store_price(i["price"], i["key"], s),
         "stock": int(jitter(f"{i['key']}:{s}:stock", 5, 60)), "embedding": v}
        for s in STORES for i, v in zip(items, vecs)])

    # Daily price history ending at today's price, drifting up to +-8% a day before.
    t = now().replace(minute=0, second=0, microsecond=0)
    await db.price_history.insert_many([
        {"ts": t - timedelta(days=d), "meta": {"store": s, "key": i["key"]},
         "price": store_price(i["price"], i["key"], s) if d == 0 else
         round(store_price(i["price"], i["key"], s) * jitter(f"{i['key']}:{s}:{d}", 0.92, 1.08) / 10) * 10}
        for s in STORES for i in items for d in range(HISTORY_DAYS)])

    vecs = await embed([f"{r['name_mn']} {r['name_en']} {' '.join(r['tags'])} "
                        f"{' '.join(g['key'] for g in r['ingredients'])}" for r in recipes])
    await db.recipes.insert_many([{**{k: v for k, v in r.items() if k != "id"}, "_id": r["id"], "embedding": e}
                                  for r, e in zip(recipes, vecs)])

    await db.branches.insert_many([{**{k: v for k, v in b.items() if k != "lonlat"},
                                    "location": {"type": "Point", "coordinates": b["lonlat"]}} for b in branches])

    if not catalog_only or not await db.pantry.find_one({"user_id": USER["_id"]}):
        await reset_household()

    await db.pantry.create_index([("user_id", 1), ("key", 1)], unique=True)
    await db.products.create_index([("store", 1), ("key", 1)], unique=True)
    await db.products.create_index([("key", 1), ("price", 1)])
    await db.branches.create_index([("location", "2dsphere")])
    await db.orders.create_index("idempotency_key", unique=True)
    await db.orders.create_index("created", expireAfterSeconds=RESERVE_TTL, partialFilterExpression={"status": "reserved"})
    await db.payments.create_index("plan_id")
    await db.offers.create_index("expires", expireAfterSeconds=0)  # store offers lapse by themselves
    await db.audit_log.create_index([("plan_id", 1), ("ts", 1)])
    await db.agent_memory.create_index([("user_id", 1), ("ts", -1)])
    await db.favorites.create_index([("user_id", 1), ("ts", -1)])
    await vector_index(db.products, "products_vec", ["store"])
    await vector_index(db.recipes, "recipes_vec", ["tags"])
    try:
        await search_index(db.products, "products_text", "search", {"mappings": {"dynamic": False, "fields": {
            f: {"type": "string"} for f in ("name_mn", "name_en", "category")}}})
    except OperationFailure as e:  # Atlas M0 allows 3 search indexes per cluster, sample data may hold one
        if e.code != 20:
            raise
        print("products_text skipped (search index limit): product search runs on vectors only, without typo tolerance")
    print(f"seeded {len(items) * len(STORES)} products, {len(recipes)} recipes, {len(branches)} branches")


if __name__ == "__main__":
    import sys
    asyncio.run(main(catalog_only="--catalog" in sys.argv))
