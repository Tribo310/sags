from itertools import combinations


def packs(line, qty):
    """How many of this store's packs a quote line is for: its own count when the store sells a different pack size
    (real products: 0.91 l milk vs 1 l), else the list's."""
    return line.get("qty", qty)


def optimize(needed, quotes, max_stores):
    """needed: {key: amount}; quotes: {store: {key: {price, in_stock, name, qty?}}}, price per the store's pack.
    Picks the store subset (size <= max_stores) with the fewest missing items, then the lowest total."""
    # ponytail: brute force over store subsets, fine for <= 6 stores
    best = None
    for k in range(1, min(max_stores, len(quotes)) + 1):
        for subset in combinations(quotes, k):
            stores, missing, total = {}, [], 0
            for key, qty in needed.items():
                offers = [(quotes[s][key]["price"] * packs(quotes[s][key], qty), s) for s in subset
                          if key in quotes[s] and quotes[s][key]["in_stock"]]
                if not offers:
                    missing.append(key)
                    continue
                line_total, s = min(offers)
                q = quotes[s][key]
                stores.setdefault(s, []).append({"key": key, "name": q["name"], "qty": packs(q, qty),
                                                 "price": q["price"], "line_total": line_total})
                total += line_total
            if best is None or (len(missing), total) < (len(best["missing"]), best["total"]):
                best = {"stores": stores, "missing": missing, "total": total}
    return best
