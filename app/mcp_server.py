import os
import uuid
from datetime import timedelta

import httpx
from pydantic import BaseModel

from mcp.server.mcpserver import MCPServer

from app.common.db import agg, audit, db, in_txn, now
from app.common.embed import embed
from app.common.policy import check_items, check_payment

OSRM = os.getenv("OSRM_URL", "https://router.project-osrm.org")
mcp = MCPServer("grocery-mcp", instructions=(
    "Grocery planning tools for Ulaanbaatar households: profile and policy, recipe and product search, "
    "shopping-list aggregation, nearest store branches, driving routes and a policy-guarded sandbox wallet."))

# Joins the cheapest product (any store) for a product key as field "p".
CHEAPEST = {"from": "products", "foreignField": "key", "pipeline": [{"$sort": {"price": 1}}, {"$limit": 1}], "as": "p"}


class Meal(BaseModel):
    recipe_id: str
    servings: int


class Refused(Exception):
    pass


async def vector_stage(index, query, limit, filter=None):
    limit = max(1, min(int(limit), 50))  # the LLM picks limit; keep it sane
    stage = {"index": index, "path": "embedding", "queryVector": (await embed([query]))[0],
             "numCandidates": limit * 10, "limit": limit}
    if filter:
        stage["filter"] = filter
    return {"$vectorSearch": stage}


async def vector_search(index, query, limit, filter=None):
    return [await vector_stage(index, query, limit, filter), {"$set": {"score": {"$meta": "vectorSearchScore"}}}]


@mcp.tool()
async def get_profile(user_id: str) -> dict:
    """Household size, diet, home location, spending policy, wallet balance and remembered preferences."""
    u = await db.users.find_one({"_id": user_id}, {"password": 0, "email": 0})  # never hand credentials to the LLM
    if not u:
        return {"error": f"unknown user {user_id}"}
    u["memory"] = [m["fact"] async for m in db.agent_memory.find({"user_id": user_id}).sort("ts", -1).limit(20)]
    return u


@mcp.tool()
async def search_recipes(query: str, exclude_tags: list[str] | None = None, limit: int = 8) -> dict:
    """Semantic recipe search. exclude_tags removes e.g. ['pork']. Returns cost_per_serving at the cheapest known prices."""
    flt = {"tags": {"$nin": exclude_tags}} if exclude_tags else None
    return {"recipes": await agg(db.recipes, [
        *await vector_search("recipes_vec", query, limit, flt),
        {"$unwind": "$ingredients"},
        {"$lookup": {**CHEAPEST, "localField": "ingredients.key"}},
        {"$unwind": "$p"},
        {"$group": {"_id": "$_id", "name_mn": {"$first": "$name_mn"}, "name_en": {"$first": "$name_en"},
                    "tags": {"$first": "$tags"}, "score": {"$first": "$score"},
                    "cost_per_serving": {"$sum": {"$multiply": ["$ingredients.qty", {"$divide": ["$p.price", "$p.pack"]}]}}}},
        {"$set": {"cost_per_serving": {"$round": ["$cost_per_serving", 0]}}},
        {"$sort": {"score": -1}},
    ])}


@mcp.tool()
async def search_products(query: str, limit: int = 5) -> dict:
    """Product search across all stores (meaning + typo-tolerant text); returns item keys usable in a shopping list."""
    n = max(1, min(int(limit), 50)) * 3
    return {"products": await agg(db.products, [
        # Hybrid: reciprocal rank fusion of vector search (meaning) and fuzzy Atlas Search (spelling, exact names).
        {"$rankFusion": {"input": {"pipelines": {
            "vector": [await vector_stage("products_vec", query, n)],
            "text": [{"$search": {"index": "products_text", "text": {
                "query": query, "path": ["name_mn", "name_en", "category"], "fuzzy": {"maxEdits": 2}}}},
                     {"$limit": n}]}}}},
        {"$limit": n},
        {"$set": {"score": {"$meta": "score"}}},
        {"$group": {"_id": "$key", "name": {"$first": "$name_mn"}, "category": {"$first": "$category"},
                    "unit": {"$first": "$unit"}, "pack": {"$first": "$pack"},
                    "min_price": {"$min": "$price"}, "score": {"$max": "$score"}}},
        {"$sort": {"score": -1}}, {"$limit": limit},
        {"$project": {"_id": 0, "key": "$_id", "name": 1, "category": 1, "unit": 1, "pack": 1, "min_price": 1}},
    ])}


@mcp.tool()
async def build_list(user_id: str, meals: list[Meal]) -> dict:
    """Aggregate recipes x servings into packs to buy, with the cheapest known price and policy violations."""
    user = await db.users.find_one({"_id": user_id})
    if not user:
        return {"error": f"unknown user {user_id}"}
    servings = {}
    for m in meals:
        servings[m.recipe_id] = servings.get(m.recipe_id, 0) + m.servings
    ids, counts = list(servings), list(servings.values())
    items = await agg(db.recipes, [
        {"$match": {"_id": {"$in": ids}}},
        {"$set": {"servings": {"$arrayElemAt": [counts, {"$indexOfArray": [ids, "$_id"]}]}}},
        {"$unwind": "$ingredients"},
        {"$group": {"_id": "$ingredients.key", "amount": {"$sum": {"$multiply": ["$ingredients.qty", "$servings"]}}}},
        # Subtract what is already at home.
        {"$lookup": {"from": "pantry", "localField": "_id", "foreignField": "key",
                     "pipeline": [{"$match": {"user_id": user_id}}], "as": "have"}},
        {"$set": {"need": "$amount", "amount": {"$max": [0, {"$subtract": [
            "$amount", {"$ifNull": [{"$first": "$have.amount"}, 0]}]}]}}},
        {"$lookup": {**CHEAPEST, "localField": "_id"}},
        {"$unwind": "$p"},
        {"$project": {"_id": 0, "key": "$_id", "name": "$p.name_mn", "category": "$p.category", "unit": "$p.unit",
                      "amount": {"$round": ["$amount", 2]},
                      "from_pantry": {"$round": [{"$subtract": ["$need", "$amount"]}, 2]},
                      "qty": {"$toInt": {"$ceil": {"$round": [{"$divide": ["$amount", "$p.pack"]}, 6]}}},
                      "min_price": "$p.price"}},
        {"$set": {"est_cost": {"$multiply": ["$qty", "$min_price"]}}},
        {"$sort": {"est_cost": -1}},
    ])
    pantry = [{"key": i["key"], "name": i["name"], "used": i["from_pantry"], "unit": i["unit"]}
              for i in items if i["from_pantry"] > 0]
    items = [i for i in items if i["qty"] > 0]
    found = await db.recipes.distinct("_id", {"_id": {"$in": ids}})
    return {"items": items, "from_pantry": pantry, "estimated_total": sum(i["est_cost"] for i in items),
            "budget": user["policy"]["budget"], "unknown_recipes": sorted(set(ids) - set(found)),
            "policy_violations": check_items(user["policy"], items)}


@mcp.tool()
async def get_pantry(user_id: str) -> dict:
    """What the household has at home and how many days each item lasts at its usual daily use.
    build_list already subtracts the pantry."""
    return {"pantry": await agg(db.pantry, [
        {"$match": {"user_id": user_id}},
        {"$project": {"_id": 0, "key": 1, "name": 1, "unit": 1, "amount": {"$round": ["$amount", 2]},
                      "days_left": {"$cond": [{"$gt": ["$daily_use", 0]},
                                              {"$round": [{"$divide": ["$amount", "$daily_use"]}, 1]}, None]}}},
        {"$set": {"order": {"$ifNull": ["$days_left", 1e9]}}},  # soonest to run out first, no-daily-use items last
        {"$sort": {"order": 1, "name": 1}},
        {"$unset": "order"},
    ])}


@mcp.tool()
async def set_pantry(user_id: str, key: str, amount: float) -> dict:
    """Record how much of a product is at home, in its unit (kg, l, pcs), e.g. after 'I have 2 kg rice'. 0 = none left."""
    p = await db.products.find_one({"key": key}, {"name_mn": 1, "unit": 1})
    if not p:
        return {"error": f"unknown product {key}; use search_products to find keys"}
    await db.pantry.update_one({"user_id": user_id, "key": key},
                               {"$set": {"amount": max(0.0, amount), "name": p["name_mn"], "unit": p["unit"], "updated": now()},
                                "$setOnInsert": {"daily_use": 0}}, upsert=True)
    return {"ok": True}


@mcp.tool()
async def spending_stats(user_id: str) -> dict:
    """Spending of completed orders: totals, savings vs buying everything at the priciest store, per week, per store, top items."""
    spent = {"$sum": "$receipts.total"}
    r = await agg(db.plans, [
        {"$match": {"user_id": user_id, "state": "completed", "receipts.0": {"$exists": True}}},
        {"$facet": {
            "totals": [{"$group": {"_id": None, "orders": {"$sum": 1}, "spent": {"$sum": spent},
                                   "saved": {"$sum": {"$ifNull": ["$basket.savings", 0]}}}},
                       {"$project": {"_id": 0}}],
            "by_week": [{"$group": {"_id": {"$dateTrunc": {"date": "$created", "unit": "week"}}, "spent": {"$sum": spent}}},
                        {"$sort": {"_id": 1}}, {"$project": {"_id": 0, "week": "$_id", "spent": 1}}],
            "by_store": [{"$unwind": "$receipts"},
                         {"$group": {"_id": "$receipts.store", "spent": {"$sum": "$receipts.total"}}},
                         {"$sort": {"spent": -1}}, {"$project": {"_id": 0, "store": "$_id", "spent": 1}}],
            "top_items": [{"$unwind": "$receipts"}, {"$unwind": "$receipts.lines"},
                          {"$group": {"_id": "$receipts.lines.name", "qty": {"$sum": "$receipts.lines.qty"},
                                      "spent": {"$sum": "$receipts.lines.line_total"}}},
                          {"$sort": {"spent": -1}}, {"$limit": 5}, {"$project": {"_id": 0, "name": "$_id", "qty": 1, "spent": 1}}],
        }},
    ])
    r = r[0]
    r["totals"] = r["totals"][0] if r["totals"] else {"orders": 0, "spent": 0, "saved": 0}
    return r


@mcp.tool()
async def price_history(key: str, days: int = 30) -> dict:
    """Price trend of one product per store over the last days (time-series): current, min, avg, max,
    and whether today's price is the lowest of the period. Use for 'is now a good time to buy X?'."""
    since = now() - timedelta(days=max(1, min(int(days), 365)))
    return {"key": key, "stores": await agg(db.price_history, [
        {"$match": {"meta.key": key, "ts": {"$gte": since}}},
        {"$sort": {"ts": 1}},
        {"$group": {"_id": "$meta.store", "current": {"$last": "$price"}, "min": {"$min": "$price"},
                    "avg": {"$avg": "$price"}, "max": {"$max": "$price"}, "points": {"$sum": 1}}},
        {"$project": {"_id": 0, "store": "$_id", "current": 1, "min": 1, "max": 1, "points": 1,
                      "avg": {"$round": ["$avg", 0]}, "lowest_in_period": {"$lte": ["$current", "$min"]}}},
        {"$sort": {"current": 1}},
    ])}


@mcp.tool()
async def nearest_branches(lon: float, lat: float, stores: list[str]) -> dict:
    """Nearest branch of each given store to a point (2dsphere $geoNear)."""
    out = []
    for s in stores:
        out += await agg(db.branches, [
            {"$geoNear": {"near": {"type": "Point", "coordinates": [lon, lat]}, "distanceField": "meters",
                          "query": {"store": s}}},
            {"$limit": 1},
            {"$project": {"_id": 0, "store": 1, "name": 1, "address": 1, "hours": 1, "location": 1,
                          "meters": {"$round": ["$meters", 0]}}},
        ])
    return {"branches": out}


@mcp.tool()
async def get_route(from_lon: float, from_lat: float, to_lon: float, to_lat: float) -> dict:
    """Driving distance and time between two points (OSRM)."""
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            r = await h.get(f"{OSRM}/route/v1/driving/{from_lon},{from_lat};{to_lon},{to_lat}",
                            params={"overview": "false"})
            route = r.json()["routes"][0]
        return {"km": round(route["distance"] / 1000, 1), "minutes": round(route["duration"] / 60)}
    except Exception as e:
        return {"error": f"route unavailable: {e}"}


@mcp.tool()
async def wallet_hold(plan_id: str, store: str, amount: int) -> dict:
    """Hold money for one store order. Policy is enforced here: only an approved plan that is checking out, within budget."""
    plan = await db.plans.find_one({"_id": plan_id})
    user = plan and await db.users.find_one({"_id": plan["user_id"]})
    if not user:
        return {"ok": False, "violations": ["unknown plan"]}
    held = await agg(db.payments, [{"$match": {"plan_id": plan_id, "status": {"$in": ["held", "captured"]}}},
                                   {"$group": {"_id": None, "sum": {"$sum": "$amount"}}}])
    v = check_payment(user["policy"], plan, store, amount, held[0]["sum"] if held else 0)
    if v:
        await audit(plan_id, "wallet", "hold_rejected", args={"store": store, "amount": amount}, result={"violations": v})
        return {"ok": False, "violations": v}
    pid = uuid.uuid4().hex

    async def txn(s):
        if not await db.users.find_one_and_update({"_id": user["_id"], "wallet": {"$gte": amount}},
                                                  {"$inc": {"wallet": -amount}}, session=s):
            raise Refused("insufficient wallet balance")
        await db.payments.insert_one({"_id": pid, "plan_id": plan_id, "user_id": user["_id"], "store": store,
                                      "amount": amount, "status": "held", "ts": now()}, session=s)
    try:
        await in_txn(txn)
    except Refused as e:
        return {"ok": False, "violations": [str(e)]}
    await audit(plan_id, "wallet", "hold", result={"payment_id": pid, "store": store, "amount": amount})
    return {"ok": True, "payment_id": pid}


@mcp.tool()
async def wallet_capture(payment_id: str) -> dict:
    """Capture a held payment."""
    p = await db.payments.find_one_and_update({"_id": payment_id, "status": "held"},
                                              {"$set": {"status": "captured", "captured": now()}})
    if not p:
        return {"ok": False, "error": "payment is not held"}
    await audit(p["plan_id"], "wallet", "capture", result={"payment_id": payment_id, "amount": p["amount"]})
    return {"ok": True}


@mcp.tool()
async def wallet_refund(payment_id: str) -> dict:
    """Release a hold or refund a captured payment back to the wallet. Idempotent."""
    async def txn(s):
        p = await db.payments.find_one_and_update({"_id": payment_id, "status": {"$in": ["held", "captured"]}},
                                                  {"$set": {"status": "refunded", "refunded": now()}}, session=s)
        if p:
            await db.users.update_one({"_id": p["user_id"]}, {"$inc": {"wallet": p["amount"]}}, session=s)
        return p
    p = await in_txn(txn)
    if p:
        await audit(p["plan_id"], "wallet", "refund", result={"payment_id": payment_id, "amount": p["amount"]})
    return {"ok": True, "refunded": bool(p)}


if __name__ == "__main__":
    mcp.run("streamable-http", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8100")), streamable_http_path="/mcp")
