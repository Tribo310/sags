from app.agents.protocols.mcp import call_json


class CheckoutError(Exception):
    pass


class Wallet:
    """Sandbox wallet over MCP. Raises CheckoutError when the server refuses (policy, balance)."""

    def __init__(self, mcp):
        self.mcp = mcp

    async def _call(self, name, **args):
        d = await call_json(self.mcp, name, args)
        if not d.get("ok"):
            raise CheckoutError(f"{name}: {d.get('violations') or d.get('error')}")
        return d

    async def hold(self, plan_id, store, amount):
        return (await self._call("wallet_hold", plan_id=plan_id, store=store, amount=amount))["payment_id"]

    async def capture(self, payment_id):
        await self._call("wallet_capture", payment_id=payment_id)

    async def refund(self, payment_id):
        await self._call("wallet_refund", payment_id=payment_id)


class SoldOut(CheckoutError):
    """An item is out of stock at every store: the plan itself has to change."""
    def __init__(self, name):
        super().__init__(f"{name} is out of stock everywhere")
        self.name = name


def cheapest_other(quotes, key, exclude):
    offers = [(q[key]["price"], s) for s, q in quotes.items() if s not in exclude and key in q and q[key]["in_stock"]]
    return min(offers)[::-1] if offers else None  # (store, price)


async def undo(orders, payments, call_store, wallet, log):
    """Refund payments and cancel orders. Keeps going past individual failures so one bad call
    doesn't leave the rest charged; failures are logged for manual follow-up."""
    for p in payments:
        try:
            await wallet.refund(p)
        except Exception as e:
            await log("undo_failed", payment_id=p, error=str(e))
    for o in orders:
        try:  # an order whose reserve reply was lost is known only by its idempotency key
            await call_store(o["store"], {"skill": "cancel", "order_id": o.get("order_id"),
                                          "idempotency_key": o.get("idempotency_key")})
        except Exception as e:
            await log("undo_failed", store=o["store"], order_id=o.get("order_id"), error=str(e))


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
            key = f"{plan['_id']}:v{plan['version']}:{n}"
            try:
                r = await call_store(store, {"skill": "reserve", "plan_id": plan["_id"], "idempotency_key": key,
                                             "offer_id": (plan.get("offers") or {}).get(store),
                                             "items": [{"key": l["key"], "qty": l["qty"]} for l in lines]})
            except Exception:
                orders.append({"store": store, "idempotency_key": key})  # it may have committed: undo by key
                raise
            await log("reserve", store=store, result=r)
            if r.get("ok"):
                orders.append({"store": store, "order_id": r["order_id"], "idempotency_key": key, "total": r["total"]})
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
                    raise SoldOut(l["name"])
                s2, price = alt
                moved.setdefault(s2, []).append({**l, "price": price, "line_total": price * l["qty"]})
                await log("reroute", item=l["key"], name=l["name"], from_store=store, to_store=s2, price=price)
            queue += [(s2, ls, tried | {store}) for s2, ls in moved.items()]

        for o in orders:
            payments.append(await wallet.hold(plan["_id"], o["store"], o["total"]))
        receipts = []
        for o, p in zip(orders, payments):
            r = await call_store(o["store"], {"skill": "place_order", "order_id": o["order_id"], "payment_id": p})
            if not r.get("ok"):
                raise CheckoutError(f"{o['store']}: {r.get('error')}")
            await wallet.capture(p)
            receipts.append(r)
        return receipts
    except Exception as e:
        await log("compensate", reason=str(e))
        await undo(orders, payments, call_store, wallet, log)
        raise
