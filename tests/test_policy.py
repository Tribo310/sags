from app.common.policy import DEFAULT_POLICY, check_items, check_payment

PLAN = {"state": "checking_out", "version": 2, "approved_version": 2, "approved_total": 100000}


def test_ok():
    assert check_payment(DEFAULT_POLICY, PLAN, "nomin", 60000, 40000) == []


def test_over_budget():
    v = check_payment(DEFAULT_POLICY, {**PLAN, "approved_total": 200000}, "nomin", 100000, 60000)
    assert any("budget" in x for x in v)


def test_not_checking_out():
    assert check_payment(DEFAULT_POLICY, {**PLAN, "state": "awaiting_approval"}, "nomin", 1000, 0)


def test_plan_changed_after_approval():
    assert check_payment(DEFAULT_POLICY, {**PLAN, "approved_version": 1}, "nomin", 1000, 0)


def test_store_not_allowed():
    assert check_payment(DEFAULT_POLICY, PLAN, "gs25", 1000, 0)


def test_more_than_5_percent_over_approved():
    assert check_payment(DEFAULT_POLICY, PLAN, "nomin", 106000, 0)
    assert check_payment(DEFAULT_POLICY, PLAN, "nomin", 104000, 0) == []


def test_check_items():
    v = check_items(DEFAULT_POLICY, [
        {"key": "vodka", "category": "alcohol", "qty": 1},
        {"key": "rice", "category": "grain", "qty": 40},
        {"key": "milk", "category": "dairy", "qty": 3},
    ])
    assert set(v) == {"vodka", "rice"}
