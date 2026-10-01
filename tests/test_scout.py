import asyncio
import json

from app.agents.scout import agent as scout
from app.agents.scout.sources import turbo_decode

MILK = {"key": "milk", "name_mn": "Сүү", "name_en": "Milk", "category": "dairy", "unit": "l", "pack": 1, "price": 3400}


def test_turbo_decode_objects_arrays_and_constants():
    # {"routes/x": {"data": {"items": [{"name": "Сүү", "price": 3500}], "hasMore": false, "next": null}}}
    arr = [{"_1": 2}, "routes/x", {"_3": 4}, "data", {"_5": 6, "_10": 11, "_12": -5}, "items", [7], {"_8": 9, "_13": 14},
           "name", "Сүү", "hasMore", False, "next", "price", 3500]
    d = turbo_decode(json.dumps(arr) + "\nP1:[]")
    assert d == {"routes/x": {"data": {"items": [{"name": "Сүү", "price": 3500}], "hasMore": False, "next": None}}}


def test_keyword_score_tolerates_mongolian_suffixes():
    assert scout.keyword_score(MILK, "Сүүтэй цай") == 1
    assert scout.keyword_score({"name_mn": "Цагаан будаа"}, "Будаа Жасмин 1кг") == 1
    assert scout.keyword_score(MILK, "Талх") == 0


def ctx_with(price, pack_hint=""):
    p = {"store": "emart", "sku": "865", "name": "Сүү 3.2%", "price": price, "in_stock": True, "stock": 50,
         "pack_hint": pack_hint}
    return {"item": MILK, "shown": {"emart": {"e0": p}}, "decided": {}}


def test_price_is_converted_to_our_pack():
    ctx = ctx_with(3100)
    r = asyncio.run(scout.record_match(ctx, "emart", "e0", 0.91, 0.9))  # 910 ml for 3100 -> 1 l
    assert r == {"status": "accepted", "our_price": 3410}
    assert ctx["decided"]["emart"]["sku"] == "865"


def test_implausible_or_unsure_goes_to_review():
    assert asyncio.run(scout.record_match(ctx_with(3100), "emart", "e0", 0.091, 0.9))["status"] == "review"  # 10x
    assert asyncio.run(scout.record_match(ctx_with(3000), "emart", "e0", 0.5, 0.9))["status"] == "accepted"  # 1.8x
    assert asyncio.run(scout.record_match(ctx_with(3400), "emart", "e0", 1, 0.5))["status"] == "review"


def test_only_shown_candidates_can_be_matched():
    r = asyncio.run(scout.record_match(ctx_with(3400), "emart", "8656020764358", 1, 0.9))
    assert "error" in r


def test_finish_needs_both_stores():
    ctx = {"decided": {"emart": {}}}
    assert "nomin" in asyncio.run(scout.finish(ctx))["error"]


def test_packs_for_rounds_up_to_whole_packs():
    import os
    os.environ.setdefault("STORE", "nomin"); os.environ.setdefault("PORT", "9101")
    from app.agents.store.server import packs_for
    assert packs_for(2, 0.91) == 3 and packs_for(2, 1) == 2 and packs_for(0.2, 1) == 1 and packs_for(42, 10) == 5


def test_reroute_uses_the_other_stores_own_pack():
    from app.planner.checkout import cheapest_other
    quotes = {"a": {"milk": {"price": 3000, "in_stock": True, "qty": 3}}, "b": {"milk": {"price": 4000, "in_stock": True, "qty": 2}}}
    assert cheapest_other(quotes, "milk", 3, {"a"}) == ("b", 4000, 2)


def test_no_match_does_not_overwrite_review():
    ctx = ctx_with(3100)
    asyncio.run(scout.record_match(ctx, "emart", "e0", 0.091, 0.9))
    asyncio.run(scout.no_match(ctx, "emart", "gave up"))
    assert ctx["decided"]["emart"]["status"] == "review"


def test_two_stores_agreeing_beat_the_guessed_catalog_price():
    salt = [{"store": "nomin", "our_price": 11000, "status": "review", "price_only": True},
            {"store": "emart", "our_price": 19300, "status": "review", "price_only": True}]
    assert [m["status"] for m in scout.corroborate(salt)] == ["accepted", "accepted"]
    alone = [{"store": "nomin", "our_price": 11000, "status": "review", "price_only": True}, {"store": "emart", "status": "none"}]
    assert scout.corroborate(alone)[0]["status"] == "review"
    unsure = [{"store": "nomin", "our_price": 11000, "status": "review", "price_only": False},
              {"store": "emart", "our_price": 12000, "status": "accepted"}]
    assert scout.corroborate(unsure)[0]["status"] == "review"  # held back for confidence, not price: stays


def test_procurement_may_only_drop_or_swap_missing_items(monkeypatch):
    from app.agents import procurement

    class Plans:
        async def find_one(self, *a, **k):
            return {"shopping_list": {"items": [{"key": "beans_can", "amount": 1}]}, "needed": {"beans_can": 1, "oats": 1},
                    "basket": {"missing": ["oats"]}}

    class DB:
        plans = Plans()
    monkeypatch.setattr(procurement, "db", DB())
    ctx = {"plan_id": "p", "user": {"policy": {}}}
    r = asyncio.run(procurement.quote_stores(ctx, swap=[{"from": "beans_can", "to": "turnip"}]))
    assert "error" in r and "beans_can" in r["error"]
    r = asyncio.run(procurement.quote_stores(ctx, drop=["beans_can"]))
    assert "error" in r


def test_policy_counts_weighed_food_in_kilos():
    from app.agents.procurement import policy_qty
    assert policy_qty({"qty": 15, "pack": 0.1}) == 2  # 1.5 kg of apples is 2 kg-ish, not 15 "packs"
    assert policy_qty({"qty": 3, "pack": 0.91}) == 3  # milk cartons stay packs
    assert policy_qty({"qty": 12, "pack": 1}) == 12


def test_bulk_packs_go_to_review():
    butter = {"key": "butter", "name_mn": "Цөцгийн тос", "name_en": "Butter", "category": "dairy", "unit": "kg", "pack": 0.2,
              "price": 5800}
    ctx = {"item": butter, "decided": {}, "shown": {"nomin": {"n0": {"store": "nomin", "sku": "1", "name": "President 1 кг",
           "price": 81080, "in_stock": True, "stock": 9, "pack_hint": ""}}}}
    r = asyncio.run(scout.record_match(ctx, "nomin", "n0", 1.0, 0.95))
    assert r["status"] == "review" and "bulk" in r["why_review"]
