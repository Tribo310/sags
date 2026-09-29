from app.common.optimizer import optimize


def line(price, stock=True):
    return {"price": price, "in_stock": stock, "name": "x"}


Q = {
    "a": {"rice": line(100), "beef": line(1000)},
    "b": {"rice": line(90), "beef": line(1200)},
    "c": {"rice": line(50, stock=False)},
}


def test_one_store_cheapest_total():
    best = optimize({"rice": 2, "beef": 1}, Q, max_stores=1)
    assert list(best["stores"]) == ["a"] and best["total"] == 1200


def test_two_stores_split():
    best = optimize({"rice": 2, "beef": 1}, Q, max_stores=2)
    assert best["total"] == 1180
    assert [l["key"] for l in best["stores"]["b"]] == ["rice"]


def test_out_of_stock_is_missing():
    best = optimize({"rice": 1}, {"c": Q["c"]}, max_stores=1)
    assert best["missing"] == ["rice"] and best["total"] == 0


def test_complete_basket_beats_cheaper_incomplete():
    q = {"x": {"rice": line(10)}, "y": {"rice": line(20), "beef": line(30)}}
    best = optimize({"rice": 1, "beef": 1}, q, max_stores=1)
    assert list(best["stores"]) == ["y"] and best["missing"] == []
