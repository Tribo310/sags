import asyncio
import hashlib
import hmac
import os
import secrets
import time
import uuid
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.common.db import db
from app.common.policy import DEFAULT_POLICY

SECRET = os.environ["JWT_SECRET"]  # no default: a guessable secret lets anyone sign in as anyone
if len(SECRET) < 32:
    raise SystemExit("JWT_SECRET must be at least 32 characters (see .env.example)")
ACCESS_TTL = 15 * 60
REFRESH_TTL = 7 * 24 * 3600
SECURE = os.getenv("COOKIE_SECURE") == "1"
START_WALLET = 500000  # demo money for every new household


def _scrypt(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)


def hash_password(password):
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password, stored):
    try:
        _, salt, h = stored.split("$")
        return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt)).hex(), h)
    except (ValueError, TypeError):  # malformed or non-ASCII stored hash
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


DUMMY_HASH = hash_password(secrets.token_hex(8))  # login checks a hash even for unknown emails: same timing

router = APIRouter(prefix="/auth")


def set_cookies(resp: Response, uid: str, refresh=True):
    kw = {"httponly": True, "samesite": "lax", "secure": SECURE}
    resp.set_cookie("access_token", make_token(uid, "access", ACCESS_TTL), max_age=ACCESS_TTL, path="/", **kw)
    if refresh:  # path=/auth: the long-lived token only travels to the auth routes
        resp.set_cookie("refresh_token", make_token(uid, "refresh", REFRESH_TTL), max_age=REFRESH_TTL, path="/auth", **kw)


def current_user(access_token: Annotated[str | None, Cookie()] = None) -> str:
    uid = access_token and read_token(access_token, "access")
    if not uid:
        raise HTTPException(401, "not logged in")
    return uid


Diet = Literal["no_pork", "vegetarian"]
Store = Literal["nomin", "emart", "sansar"]
Location = tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-90, le=90)]]  # [lon, lat]
POLICY_KEYS = ("budget", "approval_threshold", "max_stores", "allowed_stores")


class RegisterIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")
    household: int = Field(ge=1, le=20)
    diet: list[Diet] = []
    budget: int = Field(ge=1000, le=10_000_000)
    location: Location


class ProfileIn(BaseModel):
    """Profile edit: every field optional, same bounds as RegisterIn plus the guardrails."""
    name: str | None = Field(None, min_length=1, max_length=80, pattern=r"\S")
    household: int | None = Field(None, ge=1, le=20)
    diet: list[Diet] | None = None
    location: Location | None = None
    budget: int | None = Field(None, ge=1000, le=10_000_000)
    approval_threshold: int | None = Field(None, ge=0, le=10_000_000)
    max_stores: int | None = Field(None, ge=1, le=3)
    allowed_stores: list[Store] | None = Field(None, min_length=1)


def profile_update(body: ProfileIn, policy: dict) -> dict:
    """$set for a profile edit. Policy fields go to dotted paths so the rest of the policy is kept."""
    fields = body.model_dump(exclude_none=True)
    merged = policy | {k: v for k, v in fields.items() if k in POLICY_KEYS}
    if merged["approval_threshold"] > merged["budget"]:
        raise ValueError(f"approval threshold {merged['approval_threshold']} is above the budget {merged['budget']}")
    out = {}
    for k, v in fields.items():
        if k in POLICY_KEYS:
            out[f"policy.{k}"] = list(dict.fromkeys(v)) if k == "allowed_stores" else v
        elif k == "location":
            out[k] = {"type": "Point", "coordinates": list(v)}
        else:
            out[k] = v.strip() if k == "name" else v
    return out


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


@router.post("/register")
async def register(body: RegisterIn, resp: Response):
    uid = uuid.uuid4().hex
    try:
        await db.users.insert_one({
            "_id": uid, "email": body.email.strip().lower(), "password": await asyncio.to_thread(hash_password, body.password),
            "name": body.name.strip(), "household": body.household, "diet": body.diet,
            "location": {"type": "Point", "coordinates": list(body.location)},
            "wallet": START_WALLET, "policy": DEFAULT_POLICY | {"budget": body.budget}})
    except DuplicateKeyError:
        raise HTTPException(409, "this email is already registered")
    set_cookies(resp, uid)
    return {"ok": True}


@router.post("/login")
async def login(body: LoginIn, resp: Response):
    u = await db.users.find_one({"email": body.email.strip().lower()}, {"password": 1})
    ok = await asyncio.to_thread(verify_password, body.password, u["password"] if u else DUMMY_HASH)
    if not (u and ok):
        raise HTTPException(401, "wrong email or password")
    set_cookies(resp, u["_id"])
    return {"ok": True}


@router.post("/refresh")
async def refresh(resp: Response, refresh_token: Annotated[str | None, Cookie()] = None):
    uid = refresh_token and read_token(refresh_token, "refresh")
    if not uid:
        raise HTTPException(401, "session expired, log in again")
    set_cookies(resp, uid, refresh=False)
    return {"ok": True}


@router.post("/logout")
async def logout(resp: Response):
    # ponytail: stateless tokens, so logout only clears cookies; add token_version on the user doc to revoke.
    resp.delete_cookie("access_token", path="/")
    resp.delete_cookie("refresh_token", path="/auth")
    return {"ok": True}


@router.get("/me")
async def me(uid: Annotated[str, Depends(current_user)]):
    u = await db.users.find_one({"_id": uid}, {"password": 0})
    if not u:  # user removed (e.g. DEMO reseed) while the token is still valid
        raise HTTPException(401, "account not found")
    return u


@router.patch("/me")
async def update_me(body: ProfileIn, uid: Annotated[str, Depends(current_user)]):
    u = await db.users.find_one({"_id": uid}, {"policy": 1})
    if not u:
        raise HTTPException(401, "account not found")
    try:
        upd = profile_update(body, u["policy"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    if upd:
        await db.users.update_one({"_id": uid}, {"$set": upd})
    return await me(uid)
