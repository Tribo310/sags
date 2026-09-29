import os
import time

os.environ.setdefault("JWT_SECRET", "test-secret-0123456789abcdef0123456789abcdef")

import jwt  # noqa: E402

from app.planner.auth import hash_password, make_token, read_token, verify_password  # noqa: E402


def test_password_roundtrip():
    h = hash_password("hunter2hunter2")
    assert h.startswith("scrypt$")
    assert verify_password("hunter2hunter2", h)
    assert not verify_password("hunter2hunter3", h)


def test_password_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_rejects_malformed_hash():
    assert not verify_password("x", "not-a-hash")


def test_token_roundtrip():
    assert read_token(make_token("u1", "access", 60), "access") == "u1"
    assert read_token(make_token("u1", "refresh", 60), "refresh") == "u1"


def test_token_type_is_checked():
    assert read_token(make_token("u1", "refresh", 60), "access") is None
    assert read_token(make_token("u1", "access", 60), "refresh") is None


def test_expired_token_rejected():
    assert read_token(make_token("u1", "access", -1), "access") is None


def test_foreign_signature_rejected():
    forged = jwt.encode({"sub": "u1", "typ": "access", "exp": int(time.time()) + 60},
                        "another-secret-0123456789abcdef0123456789", algorithm="HS256")
    assert read_token(forged, "access") is None


def test_garbage_token_rejected():
    assert read_token("not.a.jwt", "access") is None
