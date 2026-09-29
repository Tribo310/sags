import hashlib
import hmac
import os
import secrets
import time

import jwt

SECRET = os.environ["JWT_SECRET"]  # no default: a guessable secret lets anyone sign in as anyone
ACCESS_TTL = 15 * 60
REFRESH_TTL = 7 * 24 * 3600


def _scrypt(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password, stored):
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt)).hex(), h)
    except ValueError:
        return False


def make_token(uid, typ, ttl):
    t = int(time.time())
    return jwt.encode({"sub": uid, "typ": typ, "iat": t, "exp": t + ttl}, SECRET, algorithm="HS256")


def read_token(token, typ):
    """user_id from a valid, unexpired token of this type (access/refresh), else None."""
    try:
        claims = jwt.decode(token, SECRET, algorithms=["HS256"], options={"require": ["exp", "sub", "typ"]})
    except jwt.InvalidTokenError:
        return None
    return claims["sub"] if claims["typ"] == typ else None
