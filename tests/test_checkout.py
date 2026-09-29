import asyncio

import pytest

from app.planner.checkout import CheckoutError, checkout

QUOTES = {
    "nomin": {"rice": {"price": 4500, "in_stock": True, "name": "rice"}, "beef": {"price": 22000, "in_stock": True, "name": "beef"}},
    "emart": {"rice": {"price": 4700, "in_stock": True, "name": "rice"}, "beef": {"price": 23000, "in_stock": True, "name": "beef"}},
}
PLAN = {"_id": "p1", "version": 1, "quotes": QUOTES, "basket": {"stores": {"nomin": [
    {"key": "rice", "name": "rice", "qty": 2, "price": 4500, "line_total": 9000},
    {"key": "beef", "name": "beef", "qty": 1, "price": 22000, "line_total": 22000}]}}}


class FakeStores:
    def __init__(self, out_of_stock=(), place_fails=False, reserve_times_out=()):
        self.oos, self.place_fails, self.timeout = set(out_of_stock), place_fails, set(reserve_times_out)
        self.orders, self.by_key = {}, {}

    async def __call__(self, store, p):
        if p["skill"] == "reserve":
            missing = [i["key"] for i in p["items"] if (store, i["key"]) in self.oos]
            if missing:
                return {"ok": False, "missing": missing}
            oid = f"{store}-{len(self.orders)}"
            self.orders[oid], self.by_key[p["idempotency_key"]] = "reserved", oid
            if store in self.timeout:  # store committed, but the reply never arrives
                raise TimeoutError()
            return {"ok": True, "order_id": oid, "total": sum(QUOTES[store][i["key"]]["price"] * i["qty"] for i in p["items"])}
        if p["skill"] == "place_order":
            if self.place_fails:
                return {"ok": False, "error": "order is not reserved"}
            self.orders[p["order_id"]] = "placed"
            return {"ok": True, "store": store, "order_id": p["order_id"], "payment_id": p["payment_id"]}
        if p["skill"] == "cancel":
            self.orders[p.get("order_id") or self.by_key[p["idempotency_key"]]] = "cancelled"
            return {"ok": True}


class FakeWallet:
    def __init__(self, reject=False, refund_fails=False):
        self.reject, self.refund_fails, self.p = reject, refund_fails, {}

    async def hold(self, plan_id, store, amount):
        if self.reject:
            raise CheckoutError("over budget")
        pid = f"pay{len(self.p)}"
        self.p[pid] = "held"
        return pid

    async def capture(self, pid):
        self.p[pid] = "captured"

    async def refund(self, pid):
        if self.refund_fails:
            raise CheckoutError("wallet down")
        self.p[pid] = "refunded"


async def nolog(action, **kw):
    pass


def run(stores, wallet):
    return asyncio.run(checkout(PLAN, stores, wallet, nolog))


def test_happy_path():
    stores, wallet = FakeStores(), FakeWallet()
    receipts = run(stores, wallet)
    assert [r["store"] for r in receipts] == ["nomin"]
    assert wallet.p == {"pay0": "captured"}


def test_reroutes_out_of_stock_item():
    stores, wallet = FakeStores(out_of_stock={("nomin", "beef")}), FakeWallet()
    receipts = run(stores, wallet)
    assert sorted(r["store"] for r in receipts) == ["emart", "nomin"]
    assert set(wallet.p.values()) == {"captured"}


def test_out_everywhere_cancels_and_charges_nothing():
    stores, wallet = FakeStores(out_of_stock={("nomin", "beef"), ("emart", "beef")}), FakeWallet()
    with pytest.raises(CheckoutError):
        run(stores, wallet)
    assert wallet.p == {}
    assert set(stores.orders.values()) == {"cancelled"}


def test_wallet_rejection_cancels_reservations():
    stores, wallet = FakeStores(), FakeWallet(reject=True)
    with pytest.raises(CheckoutError):
        run(stores, wallet)
    assert set(stores.orders.values()) == {"cancelled"}


def test_failed_place_order_is_not_captured():
    stores, wallet = FakeStores(place_fails=True), FakeWallet()
    with pytest.raises(CheckoutError):
        run(stores, wallet)
    assert wallet.p == {"pay0": "refunded"}
    assert set(stores.orders.values()) == {"cancelled"}


def test_reserve_timeout_still_cancels_by_idempotency_key():
    stores, wallet = FakeStores(reserve_times_out={"nomin"}), FakeWallet()
    with pytest.raises(TimeoutError):
        run(stores, wallet)
    assert set(stores.orders.values()) == {"cancelled"}


def test_undo_continues_past_failures():
    stores, wallet = FakeStores(place_fails=True), FakeWallet(refund_fails=True)
    with pytest.raises(CheckoutError):
        run(stores, wallet)
    assert set(stores.orders.values()) == {"cancelled"}  # cancel still ran after the refund failed
