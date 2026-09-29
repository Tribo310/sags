from app.common.state import ALLOWED, can


def test_normal_flow():
    path = ["planning", "awaiting_approval", "approved", "checking_out", "completed", "cancelled"]
    assert all(can(a, b) for a, b in zip(path, path[1:]))


def test_cannot_skip_approval():
    assert not can("planning", "checking_out")
    assert not can("awaiting_approval", "checking_out")
    assert not can("planning", "approved")


def test_cancelled_is_terminal():
    assert not any(can("cancelled", s) for s in ALLOWED)
