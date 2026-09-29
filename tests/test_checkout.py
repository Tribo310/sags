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
    def __init__(self, out_of_stock=()):
        self.oos, self.orders = set(out_of_stock), {}

    async def __call__(self, store, p):
        if p["skill"] == "reserve":
            missing = [i["key"] for i in p["items"] if (store, i["key"]) in self.oos]
            if missing:
                return {"ok": False, "missing": missing}
            oid = f"{store}-{len(self.orders)}"
            self.orders[oid] = "reserved"
            return {"ok": True, "order_id": oid, "total": sum(QUOTES[store][i["key"]]["price"] * i["qty"] for i in p["items"])}
        if p["skill"] == "place_order":
            self.orders[p["order_id"]] = "placed"
            return {"ok": True, "store": store, "order_id": p["order_id"], "payment_id": p["payment_id"]}
        if p["skill"] == "cancel":
            self.orders[p["order_id"]] = "cancelled"
            return {"ok": True}


class FakeWallet:
    def __init__(self, reject=False):
        self.reject, self.p = reject, {}

    async def hold(self, plan_id, store, amount):
        if self.reject:
            raise CheckoutError("over budget")
        pid = f"pay{len(self.p)}"
        self.p[pid] = "held"
        return pid

    async def capture(self, pid):
        self.p[pid] = "captured"

    async def refund(self, pid):
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
