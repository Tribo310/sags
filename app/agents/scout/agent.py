"""Price scout: matches each catalog item to a real product in each store's listing and turns its price into ours.

Code finds candidates (keyword filter, then embedding similarity); the LLM (a ReAct loop per item) decides which
candidate is really the same everyday product and how big its pack is in our unit ("910гр" of milk = 0.91 l); code
checks the result for plausibility (implausible or unsure -> review queue) and writes the accepted real products to
the catalog as the stores sell them."""
import asyncio
import json
import math
import uuid

from app.agents.core.react import Agent, Tool, run
from app.agents.scout import sources
from app.common.db import audit, db, now
from app.common.embed import embed

STORES = ("nomin", "emart")
MIN_CONFIDENCE = 0.7
# A converted price outside this ratio to the catalog's price goes to review. Wide on purpose: the catalog's prices were
# guesses (salt at 900₮/kg, really ~6,000₮); it only catches pack sizes misread by 10x.
PLAUSIBLE = (1 / 5, 10)
KEYWORD_POOL = 100  # candidates the keyword filter keeps before the embedding ranks them (ties are common)
SHOWN = 8  # candidates the model sees per store
WEIGHT_STEP = 0.1  # kg: a product sold by weight is bought in 100 g steps, not whole kilos

SYSTEM = """You match one grocery item from our catalog to real products in two Mongolian stores' listings.
For each store (nomin, emart): call candidates, then record_match for the right one or no_match. Then finish.
Rules:
- The same product type in its plain everyday form: milk = plain drinking cow milk (not flavoured, powdered, kefir,
  condensed); flour = wheat flour; beef = beef meat (not sausage, jerky, dumplings); a dish or a snack that contains
  the item is not the item.
- Any pack size is fine: the store sells its own packs and we buy as many as needed. Never reject a candidate for
  its size. Prefer the most common household pack and an ordinary brand over premium or bulk. A special variety (rye or
  whole-grain flour, flavoured, organic, omega-3) only if our item names it or nothing plain is there.
- pack_in_our_unit: the candidate's package size in OUR unit (given in the task). Read it from the name or pack_hint:
  "1л" milk -> 1 (unit l); "910гр" milk -> 0.91 (dairy: 1 g ~ 1 ml); "500г" sausage -> 0.5 (unit kg); eggs sold as
  "10ш" -> 10 (unit pcs). If the size is not in the text, estimate only when it's obvious (a loaf of bread = 1 pcs),
  otherwise no_match.
- confidence: 0-1, how sure you are it's the same product type AND the pack size is right.
- Don't judge the price: real prices differ from our catalog and code checks plausibility. Never reject a candidate
  because it's more expensive; only because it's a different product or its size is unknown.""" 


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def keyword_score(item, name):
    """How many of the item's words start a word of the product name (Mongolian suffixes: "сүү" ~ "сүүтэй")."""
    have = sources.words(name)
    return sum(any(h.startswith(w[:4]) for h in have) for w in sources.words(item["name_mn"]))


async def rank(item, products):
    """Keyword filter, then embedding similarity to the item's names."""
    pool = sorted(products, key=lambda p: -keyword_score(item, p["name"]))[:KEYWORD_POOL]
    pool = [p for p in pool if keyword_score(item, p["name"]) > 0] or pool[:10]
    if not pool:
        return []
    vecs = await embed([f"{item['name_mn']} {item['name_en']}"] + [f"{p['name']} {p['pack_hint']}" for p in pool])
    q = vecs[0]
    return [p for _, p in sorted(zip((cosine(q, v) for v in vecs[1:]), pool), key=lambda x: -x[0])][:SHOWN]


async def candidates(ctx, store):
    item = ctx["item"]
    if store not in ctx["shown"]:
        if store == "nomin":
            found = await rank(item, ctx["nomin"])
        else:
            async with ctx["emart_lock"]:  # one Emart search at a time, spaced out
                found = await rank(item, await sources.search_emart(ctx["http"], item["name_mn"]))
                await asyncio.sleep(sources.DELAY)
            await db.crawl_raw.bulk_write([_upsert(p) for p in found]) if found else None
        ctx["shown"][store] = {f"{store[0]}{i}": p for i, p in enumerate(found)}
    shown = ctx["shown"][store]
    if not shown:
        return {"candidates": [], "hint": "nothing found: no_match"}
    return {"candidates": [{"id": i, "name": p["name"], "pack_hint": p["pack_hint"], "price": p["price"],
                            "in_stock": p["in_stock"]} for i, p in shown.items()]}


async def record_match(ctx, store, id, pack_in_our_unit, confidence, reason=""):
    p = ctx["shown"].get(store, {}).get(id)
    if not p:
        return {"error": f"{id} is not one of the {store} candidates; call candidates first and use its ids"}
    item, pack = ctx["item"], float(pack_in_our_unit)
    if pack <= 0:
        return {"error": "pack_in_our_unit must be > 0"}
    our_price = round(p["price"] * item["pack"] / pack / 10) * 10  # the candidate's price for one of OUR packs
    ratio = our_price / item["price"]
    sure, plausible = float(confidence) >= MIN_CONFIDENCE, PLAUSIBLE[0] <= ratio <= PLAUSIBLE[1]
    status = "accepted" if sure and plausible else "review"
    ctx["decided"][store] = {"store": store, "key": item["key"], "sku": p["sku"], "name": p["name"],
                             "pack_in_our_unit": pack, "store_price": p["price"], "our_price": our_price,
                             "in_stock": p["in_stock"], "stock": p["stock"], "url": p.get("url"),
                             "by_weight": bool(p.get("by_weight")),
                             "confidence": float(confidence), "reason": reason, "status": status,
                             "price_only": sure and not plausible}
    return {"status": status, "our_price": our_price,
            **({"why_review": f"price {our_price} is {ratio:.1f}x our catalog's {item['price']}",
                "note": "recorded for a person to review; this store is decided, move on"}
               if status == "review" else {})}


async def no_match(ctx, store, reason):
    if ctx["decided"].get(store, {}).get("status") == "review":  # a person decides that one; the model gave up on it
        return {"ok": True, "note": f"{store} stays in the review queue"}
    ctx["decided"][store] = {"store": store, "key": ctx["item"]["key"], "status": "none", "reason": reason}
    return {"ok": True}


async def finish(ctx, note=""):
    if missing := [s for s in STORES if s not in ctx["decided"]]:
        return {"error": f"decide {', '.join(missing)} first (record_match or no_match)"}
    return {"decided": {s: d["status"] for s, d in ctx["decided"].items()}}


def agent():
    store = {"type": "string", "enum": list(STORES)}
    return Agent("scout", SYSTEM, max_steps=10, tools=[
        Tool("candidates", "Products from this store's listing that may be the item.", candidates, {"store": store},
             ["store"]),
        Tool("record_match", "The candidate that is the item, with its pack size in our unit.", record_match,
             {"store": store, "id": {"type": "string"}, "pack_in_our_unit": {"type": "number"},
              "confidence": {"type": "number"}, "reason": {"type": "string"}},
             ["store", "id", "pack_in_our_unit", "confidence"]),
        Tool("no_match", "None of this store's candidates is the item.", no_match,
             {"store": store, "reason": {"type": "string"}}, ["store", "reason"]),
        Tool("finish", "Both stores decided.", finish, {"note": {"type": "string"}}, final=True),
    ])


def _upsert(p):
    from pymongo import UpdateOne
    return UpdateOne({"_id": f"{p['store']}:{p['sku']}"}, {"$set": {**p, "fetched_at": now()}}, upsert=True)


async def match_item(ctx, item):
    c = {**ctx, "item": item, "shown": {}, "decided": {}}
    task = (f"Our item: {item['name_mn']} ({item['name_en']}), category {item['category']}. "
            f"Our unit: {item['unit']} (give pack_in_our_unit in {item['unit']}; any pack size is fine).")
    try:
        await run(agent(), c, task)
    except Exception as e:
        await audit(ctx["plan_id"], "scout", "error", result={"error": f"{item['key']}: {e!r}"})
    return corroborate([c["decided"].get(s) or {"store": s, "key": item["key"], "status": "none", "reason": "undecided"}
                        for s in STORES])


def corroborate(decided, within=3):
    """A match held back only for its price (vs the catalog's guessed price) is accepted when the other store's real
    price for the same item agrees within `within`x: two real stores beat one made-up number."""
    priced = [m for m in decided if m.get("our_price")]
    for m in decided:
        if m.get("price_only") and m["status"] == "review":
            others = [o["our_price"] for o in priced if o is not m]
            if any(1 / within <= m["our_price"] / o <= within for o in others):
                m["status"], m["reason"] = "accepted", f"{m.get('reason', '')} (price confirmed by the other store)"
    return decided


async def apply(matches, items):
    """Real products replace the simulated catalog: one per store and item, as the store sells it (its name, its pack
    in our unit, its price). A store-item without an accepted match isn't sold there; Sansar, with no online shop,
    sells nothing. Simulated price history goes too, so trends only ever show real prices."""
    catalog = {i["key"]: i for i in load_items()}
    accepted = [m for m in matches if m["status"] == "accepted"]
    ts, real = now(), set()
    vecs = await embed([f"{m['name']} {catalog[m['key']]['name_en']} {catalog[m['key']]['category']}" for m in accepted])
    for m, vec in zip(accepted, vecs):
        c, _id = catalog[m["key"]], f"{m['store']}:{m['key']}"
        name, pack, price = m["name"], m["pack_in_our_unit"], m["store_price"]
        if m.get("by_weight") and c["unit"] == "kg":  # weighed at the counter: any amount in WEIGHT_STEP steps
            price, pack = round(price * WEIGHT_STEP), WEIGHT_STEP  # a by-weight price is per kg, whatever pack was read
            name = f"{name} (жингээр, {WEIGHT_STEP * 1000:.0f}гр)"
        real.add(_id)
        await db.products.replace_one({"_id": _id}, {
            "store": m["store"], "key": m["key"], "name_mn": name, "name_en": c["name_en"],
            "category": c["category"], "unit": c["unit"], "pack": pack, "price": price,
            "stock": m["stock"] if m["in_stock"] else 0, "embedding": vec,
            "source": {"kind": "crawled", "sku": m["sku"], "url": m.get("url"), "confidence": m["confidence"],
                       "name": m["name"], "store_price": m["store_price"], "by_weight": bool(m.get("by_weight")),
                       "fetched_at": ts}}, upsert=True)
        await db.price_history.insert_one({"ts": ts, "meta": {"store": m["store"], "key": m["key"], "src": "crawl"},
                                           "price": price})
    # gone: every simulated product, and this run's items no store sells (any more)
    done = {i["key"] for i in items}
    gone = [p["_id"] async for p in db.products.find({"$or": [{"source.kind": {"$ne": "crawled"}},
                                                               {"key": {"$in": list(done)}}]}, {"_id": 1})
            if p["_id"] not in real]
    await db.products.delete_many({"_id": {"$in": gone}})
    for g in gone:
        store, key = g.split(":", 1)
        await db.price_history.delete_many({"meta.store": store, "meta.key": key})
    await db.price_history.delete_many({"meta.src": {"$ne": "crawl"}})  # seeded history is simulated
    return {"real": len(real), "removed": len(gone)}


async def scout(items, crawl=True, write=True, parallel=4, from_run=None, log=print):
    if from_run:  # reuse an earlier run's matches (no crawling, no LLM)
        matches = [m async for m in db.crawl_matches.find({"run_id": from_run, "key": {"$in": [i["key"] for i in items]}},
                                                          {"_id": 0})]
        applied = await apply(matches, items) if write else {}
        return {"run_id": from_run, "items": len(items), "applied": applied,
                **{s: sum(1 for m in matches if m["status"] == s) for s in ("accepted", "review", "none")}}, matches
    run_id = uuid.uuid4().hex[:8]
    ctx = {"plan_id": f"scout:{run_id}", "emart_lock": asyncio.Lock()}
    async with sources.client() as h:
        ctx["http"] = h
        if crawl:
            products = await sources.crawl_nomin(h, log)
            await db.crawl_raw.bulk_write([_upsert(p) for p in products])
        ctx["nomin"] = [p async for p in db.crawl_raw.find({"store": "nomin"}, {"_id": 0, "fetched_at": 0})]
        log(f"nomin listing: {len(ctx['nomin'])} products")
        sem = asyncio.Semaphore(parallel)

        async def one(item):
            async with sem:
                out = await match_item(ctx, item)
                log(f"  {item['key']:12} " + "  ".join(f"{m['store']}: {m['status']}"
                                                        + (f" {m['our_price']}₮ ({m['name'][:30]})" if m.get("name") else "")
                                                        for m in out))
                return out
        matches = [m for ms in await asyncio.gather(*(one(i) for i in items)) for m in ms]
    await db.crawl_matches.insert_many([{**m, "run_id": run_id, "ts": now()} for m in matches])
    applied = await apply(matches, items) if write else {}
    summary = {"run_id": run_id, "items": len(items), "applied": applied,
               **{s: sum(1 for m in matches if m["status"] == s) for s in ("accepted", "review", "none")}}
    await audit(ctx["plan_id"], "scout", "summary", result=summary)
    return summary, matches


def load_items(keys=None):
    import pathlib
    items = json.loads((pathlib.Path(__file__).parents[3] / "seed" / "items.json").read_text())
    return [i for i in items if not keys or i["key"] in keys]
