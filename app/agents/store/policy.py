"""Store-side negotiation rules. The store agent's LLM decides what to offer; these limits are enforced in code."""
import math

# Each store negotiates differently: how deep it may cut and how much stock makes an item worth discounting.
POLICIES = {
    "nomin": {"max_discount": 0.05, "overstock": 40,
              "persona": "Premium supermarket. Protect your margin: discount only when a competitor is clearly cheaper."},
    "emart": {"max_discount": 0.12, "overstock": 25,
              "persona": "Price fighter. Win the basket: beat competitor prices wherever your limits allow."},
    "sansar": {"max_discount": 0.08, "overstock": 30,
               "persona": "Neighbourhood store. Clear overstock and offer good substitutes for anything you lack."},
}
DEFAULT = {"max_discount": 0.05, "overstock": 40, "persona": "Grocery store."}


def policy(store):
    return POLICIES.get(store, DEFAULT)


def floor_price(pol, price):
    """Lowest price the store may offer, rounded up to 10₮."""
    return math.ceil(price * (1 - pol["max_discount"]) / 10) * 10


def discountable(pol, line):
    return line["stock"] >= pol["overstock"]


def clamp_offer(pol, lines, discounts):
    """lines: {key: {price, stock}} the store can fill; discounts: [{key, price}] from the LLM.
    Returns {key: price}: only overstocked items, never below the floor, only real cuts."""
    out = {}
    for d in discounts or []:
        line = lines.get(d.get("key")) if isinstance(d, dict) else None
        if not line or not discountable(pol, line):
            continue
        try:
            price = int(d["price"])
        except (KeyError, TypeError, ValueError):
            continue
        price = max(price, floor_price(pol, line["price"]))
        if price < line["price"]:
            out[d["key"]] = price
    return out


def rule_offer(pol, lines, competitor):
    """No LLM (demo mode or the model failed): on overstocked items, undercut a cheaper competitor by 3%
    (rounded down to 10₮), down to the floor."""
    return clamp_offer(pol, lines, [{"key": k, "price": int(competitor[k] * 0.97) // 10 * 10} for k, l in lines.items()
                                    if k in competitor and competitor[k] <= l["price"]])
