from itertools import combinations


def optimize(needed, quotes, max_stores):
    """needed: {key: qty}; quotes: {store: {key: {price, in_stock, name}}}.
    Picks the store subset (size <= max_stores) with the fewest missing items, then the lowest total."""
    # ponytail: brute force over store subsets, fine for <= 6 stores
    best = None
    for k in range(1, min(max_stores, len(quotes)) + 1):
        for subset in combinations(quotes, k):
            stores, missing, total = {}, [], 0
            for key, qty in needed.items():
                offers = [(quotes[s][key]["price"], s) for s in subset
                          if key in quotes[s] and quotes[s][key]["in_stock"]]
                if not offers:
                    missing.append(key)
                    continue
                price, s = min(offers)
                stores.setdefault(s, []).append({"key": key, "name": quotes[s][key]["name"], "qty": qty,
                                                 "price": price, "line_total": price * qty})
                total += price * qty
            if best is None or (len(missing), total) < (len(best["missing"]), best["total"]):
                best = {"stores": stores, "missing": missing, "total": total}
    return best
