import os
import uuid
from datetime import timedelta

import uvicorn
from fastapi import FastAPI
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from app.common.db import client, db, now

STORE = os.environ["STORE"]
PORT = int(os.environ["PORT"])
NAMES = {"nomin": "Nomin Supermarket", "emart": "E-Mart", "sansar": "Sansar Supermarket"}


class OutOfStock(Exception):
    def __init__(self, keys):
        self.keys = keys


async def quote_basket(d):
    keys = [i["key"] for i in d["items"]]
    prods = {p["key"]: p async for p in db.products.find({"store": STORE, "key": {"$in": keys}}, {"embedding": 0})}
    lines = []
    for i in d["items"]:
        p, qty = prods.get(i["key"]), int(i["qty"])
        if p:
            lines.append({"key": p["key"], "name": p["name_mn"], "category": p["category"], "price": p["price"],
                          "qty": qty, "in_stock": p["stock"] >= qty})
    return {"ok": True, "store": STORE, "lines": lines}


async def reserve(d):
    """Decrement stock for every line and create the order in one transaction, or change nothing."""
    if o := await db.orders.find_one({"idempotency_key": d["idempotency_key"]}):
        return {"ok": True, "order_id": o["_id"], "total": o["total"]}
    order_id = uuid.uuid4().hex

    async def txn(s):
        lines, missing = [], []
        for i in d["items"]:
            qty = int(i["qty"])
            p = await db.products.find_one_and_update(
                {"store": STORE, "key": i["key"], "stock": {"$gte": qty}}, {"$inc": {"stock": -qty}},
                session=s, return_document=ReturnDocument.AFTER)
            if not p:
                missing.append(i["key"])
                continue
            lines.append({"key": p["key"], "name": p["name_mn"], "qty": qty, "price": p["price"],
                          "line_total": p["price"] * qty})
        if missing:
            raise OutOfStock(missing)  # aborts the transaction: no stock is taken
        await db.orders.insert_one({"_id": order_id, "store": STORE, "plan_id": d["plan_id"],
                                    "idempotency_key": d["idempotency_key"], "lines": lines,
                                    "total": sum(l["line_total"] for l in lines), "status": "reserved",
                                    "created": now()}, session=s)
    try:
        async with client.start_session() as s:
            await s.with_transaction(txn)
    except OutOfStock as e:
        return {"ok": False, "missing": e.keys}
    except DuplicateKeyError:  # concurrent retry with the same key won the race
        return await reserve(d)
    o = await db.orders.find_one({"_id": order_id})
    return {"ok": True, "order_id": order_id, "total": o["total"]}


async def place_order(d):
    o = await db.orders.find_one_and_update(
        {"_id": d["order_id"], "status": "reserved"},
        {"$set": {"status": "placed", "payment_id": d["payment_id"], "placed": now()}},
        return_document=ReturnDocument.AFTER)
    if not o:
        return {"ok": False, "error": "order is not reserved"}
    return {"ok": True, "store": STORE, "order_id": o["_id"], "payment_id": o["payment_id"], "lines": o["lines"],
            "total": o["total"], "status": "placed",
            "pickup_by": (now() + timedelta(days=1)).isoformat(timespec="minutes")}


async def cancel(d):
    async def txn(s):
        o = await db.orders.find_one_and_update({"_id": d["order_id"], "status": {"$in": ["reserved", "placed"]}},
                                                {"$set": {"status": "cancelled"}}, session=s)
        for l in (o or {}).get("lines", []):
            await db.products.update_one({"store": STORE, "key": l["key"]}, {"$inc": {"stock": l["qty"]}}, session=s)
        return bool(o)
    async with client.start_session() as s:
        return {"ok": True, "cancelled": await s.with_transaction(txn)}


SKILLS = {"quote_basket": quote_basket, "reserve": reserve, "place_order": place_order, "cancel": cancel}


class StoreExecutor(AgentExecutor):
    async def execute(self, context, event_queue):
        try:
            d = get_data_parts(context.message.parts)[0]
            result = await SKILLS[d["skill"]](d)
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        await event_queue.enqueue_event(new_data_message(result, context_id=context.context_id))

    async def cancel(self, context, event_queue):
        pass


def skill(id, desc):
    return AgentSkill(id=id, name=id, description=desc, tags=["grocery", STORE],
                      input_modes=["application/json"], output_modes=["application/json"])


card = AgentCard(
    name=STORE,
    description=f"{NAMES.get(STORE, STORE)} store agent: quotes, reserves and sells groceries in Ulaanbaatar.",
    version="1.0",
    supported_interfaces=[AgentInterface(url=os.getenv("PUBLIC_URL", f"http://localhost:{PORT}/"), protocol_binding="JSONRPC")],
    capabilities=AgentCapabilities(),
    default_input_modes=["application/json"], default_output_modes=["application/json"],
    skills=[
        skill("quote_basket", "Price and stock for {items:[{key,qty}]}"),
        skill("reserve", "Atomically reserve stock: {plan_id, idempotency_key, items}. Returns order_id or missing keys."),
        skill("place_order", "Confirm a reserved order after payment: {order_id, payment_id}. Returns receipt."),
        skill("cancel", "Cancel an order and restock: {order_id}."),
    ])

app = FastAPI(title=f"{STORE} store agent")
handler = DefaultRequestHandler(agent_executor=StoreExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card),
                          jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/"))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
