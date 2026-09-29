class CheckoutError(Exception):
    pass


def cheapest_other(quotes, key, exclude):
    offers = [(q[key]["price"], s) for s, q in quotes.items() if s not in exclude and key in q and q[key]["in_stock"]]
    return min(offers)[::-1] if offers else None  # (store, price)


async def checkout(plan, call_store, wallet, log):
    """Saga: reserve every store order (rerouting out-of-stock items to the next cheapest store),
    then hold -> place -> capture. Any failure compensates: refund payments, cancel reservations."""
    quotes = plan["quotes"]
    queue = [(s, lines, set()) for s, lines in plan["basket"]["stores"].items()]  # (store, lines, stores tried)
    orders, payments, n = [], [], 0
    try:
        while queue:
            store, lines, tried = queue.pop(0)
            n += 1
            r = await call_store(store, {"skill": "reserve", "plan_id": plan["_id"],
                                         "idempotency_key": f"{plan['_id']}:v{plan['version']}:{n}",
                                         "items": [{"key": l["key"], "qty": l["qty"]} for l in lines]})
            await log("reserve", store=store, result=r)
            if r.get("ok"):
                orders.append({"store": store, "order_id": r["order_id"], "total": r["total"]})
                continue
            if "missing" not in r:
                raise CheckoutError(f"{store}: {r.get('error')}")
            missing = set(r["missing"])
            if keep := [l for l in lines if l["key"] not in missing]:
                queue.append((store, keep, tried))
            moved = {}
            for l in lines:
                if l["key"] not in missing:
                    continue
                alt = cheapest_other(quotes, l["key"], tried | {store})
                if alt is None:
                    raise CheckoutError(f"{l['name']} is out of stock everywhere")
                s2, price = alt
                moved.setdefault(s2, []).append({**l, "price": price, "line_total": price * l["qty"]})
                await log("reroute", item=l["key"], from_store=store, to_store=s2, price=price)
            queue += [(s2, ls, tried | {store}) for s2, ls in moved.items()]

        for o in orders:
            payments.append(await wallet.hold(plan["_id"], o["store"], o["total"]))
        receipts = []
        for o, p in zip(orders, payments):
            receipts.append(await call_store(o["store"], {"skill": "place_order", "order_id": o["order_id"], "payment_id": p}))
            await wallet.capture(p)
        return receipts
    except Exception as e:
        await log("compensate", reason=str(e))
        for p in payments:
            await wallet.refund(p)
        for o in orders:
            await call_store(o["store"], {"skill": "cancel", "order_id": o["order_id"]})
        raise
