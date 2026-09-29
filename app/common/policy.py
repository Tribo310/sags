DEFAULT_POLICY = {
    "budget": 150000,
    "approval_threshold": 100000,
    "max_stores": 2,
    "allowed_stores": ["nomin", "emart", "sansar"],
    "blocked_categories": ["alcohol", "tobacco"],
    "max_qty_per_item": 10,
}


def check_items(policy, lines):
    """lines: [{key, category, qty}] -> {key: reason} for every line the policy forbids."""
    out = {}
    for l in lines:
        if l["category"] in policy["blocked_categories"]:
            out[l["key"]] = f"category {l['category']} is blocked"
        elif l["qty"] > policy["max_qty_per_item"]:
            out[l["key"]] = f"qty {l['qty']} > max {policy['max_qty_per_item']}"
    return out


def check_payment(policy, plan, store, amount, already_held):
    """Server-side guard for every wallet hold. Returns a list of violations (empty = allowed)."""
    v = []
    if plan["state"] != "checking_out":
        v.append(f"plan is {plan['state']}, not checking_out")
    if plan.get("approved_version") != plan["version"]:
        v.append("plan changed after approval")
    if store not in policy["allowed_stores"]:
        v.append(f"store {store} not allowed")
    total = already_held + amount
    if total > policy["budget"]:
        v.append(f"total {total} exceeds budget {policy['budget']}")
    if total > plan.get("approved_total", 0) * 1.05:
        v.append(f"total {total} exceeds approved {plan.get('approved_total')} by more than 5%")
    return v
