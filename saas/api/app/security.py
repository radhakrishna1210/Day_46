"""Password hashing and session tokens.

Passwords: scrypt from the standard library (no native build dependency),
per-user random salt, constant-time compare. Sessions: a signed JWT in an
httpOnly cookie, carrying the user id and the ACTIVE tenant id -- switching
business re-issues it. Google sign-in and email OTP slot in later as other
ways of reaching the same issue_session().
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

SECRET_KEY = os.environ.get("RECOVA_SECRET_KEY") or secrets.token_urlsafe(48)
ALGORITHM = "HS256"
SESSION_COOKIE = "recova_session"
SESSION_DAYS = 14

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
    actual = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def issue_session(user_id: str, tenant_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "tid": tenant_id, "iat": now,
               "exp": now + timedelta(days=SESSION_DAYS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def read_session(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
