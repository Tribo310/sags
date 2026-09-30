import os

os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.common.policy import DEFAULT_POLICY  # noqa: E402
from app.planner.auth import ProfileIn, profile_update  # noqa: E402


def test_policy_fields_use_dotted_paths():
    upd = profile_update(ProfileIn(budget=200000, max_stores=1, name=" Бат "), DEFAULT_POLICY)
    assert upd == {"policy.budget": 200000, "policy.max_stores": 1, "name": "Бат"}


def test_location_becomes_geojson():
    assert profile_update(ProfileIn(location=(106.9, 47.9)), DEFAULT_POLICY) == {
        "location": {"type": "Point", "coordinates": [106.9, 47.9]}}


def test_empty_update_changes_nothing():
    assert profile_update(ProfileIn(), DEFAULT_POLICY) == {}


def test_threshold_above_budget_rejected():
    with pytest.raises(ValueError):
        profile_update(ProfileIn(approval_threshold=200000), DEFAULT_POLICY)  # budget stays 150000
    with pytest.raises(ValueError):
        profile_update(ProfileIn(budget=50000), DEFAULT_POLICY)  # threshold stays 100000


def test_bad_stores_rejected():
    with pytest.raises(ValidationError):
        ProfileIn(allowed_stores=["walmart"])
    with pytest.raises(ValidationError):
        ProfileIn(allowed_stores=[])
    with pytest.raises(ValidationError):
        ProfileIn(max_stores=4)
