from app.agents.store.policy import clamp_offer, floor_price, rule_offer

POL = {"max_discount": 0.10, "overstock": 30}
LINES = {"milk": {"price": 3000, "stock": 50}, "rice": {"price": 4000, "stock": 10}}


def test_floor_rounds_up_to_10():
    assert floor_price(POL, 3005) == 2710


def test_clamp_keeps_price_above_floor():
    assert clamp_offer(POL, LINES, [{"key": "milk", "price": 1000}]) == {"milk": 2700}


def test_clamp_drops_low_stock_unknown_and_non_cuts():
    offer = [{"key": "rice", "price": 3900}, {"key": "beer", "price": 1}, {"key": "milk", "price": 3500},
             {"key": "milk"}, "junk"]
    assert clamp_offer(POL, LINES, offer) == {}


def test_rule_offer_undercuts_cheaper_competitor_only():
    assert rule_offer(POL, LINES, {"milk": 2900, "rice": 3000}) == {"milk": 2810}  # rice isn't overstocked
    assert rule_offer(POL, LINES, {"milk": 3500}) == {}  # already cheapest
    assert rule_offer(POL, LINES, {"milk": 2000}) == {"milk": 2700}  # can't follow below the floor
