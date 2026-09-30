"""A store's sales agent: a ReAct loop that answers a shopper's basket with a counter-offer. The LLM decides what to
offer; policy.py limits it, and the accepted prices are saved for reserve to honour."""
import json
import math
import os
import uuid
from datetime import timedelta

from app.agents.core.react import Agent, Tool, run
from app.agents.store.policy import clamp_offer, discountable, floor_price, policy, rule_offer
from app.common.db import audit, db, now
from app.common.embed import embed

DEMO = os.getenv("DEMO") == "1"  # deterministic offers, no LLM
OFFER_TTL = timedelta(hours=1)
NAMES = {"nomin": "Nomin Supermarket", "emart": "E-Mart", "sansar": "Sansar Supermarket"}

SYSTEM = """You are the sales agent of {name}, a grocery store in Ulaanbaatar. A shopper's procurement agent sent you
its basket over A2A, with the best price a competitor offered for each item. Win as much of the basket as you can.
{persona}
Work step by step: for items you lack, call find_substitute and pick good in-stock replacements. Then make_offer once.
Limits, enforced in code (anything outside them is dropped): only lines with discountable=true may get a lower price,
never below their floor_price. Undercutting a competitor a little is enough; don't give away more than needed.
make_offer.message: one or two sentences in Mongolian to the shopper about what you offer."""


async def find_substitute(store, key, qty=1):
    """Vector search the store's catalog for in-stock products like `key` (named by any store's product)."""
    ref = await db.products.find_one({"key": key}, {"name_mn": 1, "name_en": 1, "category": 1})
    if not ref:
        return {"error": f"unknown item {key}"}
    vec = (await embed([f"{ref['name_mn']} {ref['name_en']} {ref['category']}"]))[0]
    found = await (await db.products.aggregate([
        {"$vectorSearch": {"index": "products_vec", "path": "embedding", "queryVector": vec, "numCandidates": 60,
                           "limit": 6, "filter": {"store": store}}},
        {"$match": {"key": {"$ne": key}, "stock": {"$gte": qty}}},
        {"$project": {"_id": 0, "key": 1, "name": "$name_mn", "category": 1, "price": 1,
                      "score": {"$meta": "vectorSearchScore"}}}])).to_list()
    return {"for": ref["name_mn"], "substitutes": found[:3]}


async def make_offer(ctx, discounts, substitutes, message):
    """Validate the offer against the store's policy, save it and return what the shopper's agent gets back."""
    store, lines, lacking, keys = ctx["store"], ctx["lines"], ctx["lacking"], ctx["keys"]
    prices = clamp_offer(policy(store), lines, discounts)
    subs = []
    for s in substitutes or []:
        p = await db.products.find_one({"store": store, "key": s.get("key"), "stock": {"$gt": 0}}, {"embedding": 0})
        if p and s.get("for_key") in lacking and p["key"] not in keys:
            subs.append({"for_key": s["for_key"], "key": p["key"], "name": p["name_mn"], "price": p["price"]})
    offer = [{"key": k, "name": lines[k]["name"], "from": lines[k]["price"], "to": v} for k, v in prices.items()]
    offer_id = uuid.uuid4().hex
    await db.offers.insert_one({"_id": offer_id, "store": store, "plan_id": ctx["plan_id"], "prices": prices,
                                "created": now(), "expires": now() + OFFER_TTL})
    return {"ok": True, "store": store, "offer_id": offer_id, "prices": prices, "discounts": offer,
            "substitutes": subs, "message": message or (f"{len(offer)} бараанд хямдрал санал болгож байна." if offer else "")}


MAX_SUBSTITUTES = 4  # items a store looks for replacements of; with a sparse real catalog it lacked dozens


def agent(store, lines, lacking):
    """Tools whose arguments can only name what's in play: enums instead of free text. Before this, models searched
    for substitutes of made-up items ("fruit", "cake") over and over."""
    pol = policy(store)
    lacking = lacking[:MAX_SUBSTITUTES]
    can_cut = sorted(k for k, l in lines.items() if discountable(pol, l))
    item = {"type": "object", "properties": {"key": {"type": "string", "enum": can_cut or [""]},
                                             "price": {"type": "integer"}}, "required": ["key", "price"]}
    sub = {"type": "object", "properties": {"for_key": {"type": "string", "enum": lacking or [""]},
                                            "key": {"type": "string"}}, "required": ["for_key", "key"]}
    tools = [Tool("find_substitute", "Search your own shelves for in-stock products similar to an item you lack.",
                  lambda ctx, key: find_substitute(store, key), {"key": {"type": "string", "enum": lacking}}, ["key"])
             ] if lacking else []
    return Agent("store", SYSTEM.format(name=NAMES.get(store, store), persona=pol["persona"]), actor=store, max_steps=6,
                 tools=[*tools, Tool("make_offer", "Send your final offer to the shopper's agent: discounted prices "
                                     "(only discountable lines) and substitutes (only for items you lack).", make_offer,
                                     {"discounts": {"type": "array", "items": item},
                                      "substitutes": {"type": "array", "items": sub}, "message": {"type": "string"}},
                                     ["discounts", "substitutes", "message"], final=True)])


async def rules(ctx, competitor):
    """No LLM (demo mode, or the model failed): undercut cheaper competitors, first substitute for each gap."""
    subs = []
    for k in ctx["lacking"][:MAX_SUBSTITUTES]:
        if found := (await find_substitute(ctx["store"], k)).get("substitutes"):
            subs.append({"for_key": k, "key": found[0]["key"]})
    discounts = [{"key": k, "price": p} for k, p in rule_offer(policy(ctx["store"]), ctx["lines"], competitor).items()]
    result = await make_offer(ctx, discounts, subs, "")
    await audit(ctx["plan_id"], ctx["store"], "make_offer", result=result, reason="rule book", model="rules")
    return result


async def negotiate(store, d):
    """Counter-offer for {plan_id, items:[{key, amount}], competitor:{key: best other price per unit}}."""
    keys = [i["key"] for i in d["items"]]
    prods = {p["key"]: p async for p in db.products.find({"store": store, "key": {"$in": keys}}, {"embedding": 0})}
    lines, lacking, competitor = {}, [], {}
    for i in d["items"]:
        p = prods.get(i["key"])
        qty = p and max(1, math.ceil(float(i["amount"]) / p["pack"] - 1e-9))
        if p and p["stock"] >= qty:
            lines[p["key"]] = {"name": p["name_mn"], "qty": qty, "price": p["price"], "stock": p["stock"]}
            if (unit := (d.get("competitor") or {}).get(p["key"])) is not None:  # per unit -> per one of our packs
                competitor[p["key"]] = round(unit * p["pack"])
        else:
            lacking.append(i["key"])
    ctx = {"plan_id": d["plan_id"], "store": store, "lines": lines, "lacking": lacking, "keys": keys}
    if DEMO or not lines:  # nothing of the basket on the shelf: no LLM needed to say so
        return await rules(ctx, competitor)
    pol = policy(store)
    view = [{"key": k, "name": l["name"], "qty": l["qty"], "your_price": l["price"], "stock": l["stock"],
             "competitor_price": competitor.get(k), "discountable": discountable(pol, l),
             "floor_price": floor_price(pol, l["price"])} for k, l in lines.items()]
    try:
        result = await run(agent(store, lines, lacking), ctx,
                           json.dumps({"basket": view, "you_lack": lacking}, ensure_ascii=False))
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    if "error" in result:  # a store that can't think still answers, with its rule book
        print(f"{store} negotiator failed, using rules: {result['error']}")
        return await rules(ctx, competitor)
    return result
