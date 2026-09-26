"""One-time email codes: sign-in, email verification, password reset.

  * 6 digits from the OS CSPRNG; only an HMAC of it is stored (keyed with the
    session secret), so a leaked database does not leak live codes.
  * Valid for CODE_MINUTES, usable once; a new code for the same email and
    purpose retires the previous one.
  * At most MAX_ATTEMPTS wrong guesses per code, and at most MAX_PER_WINDOW
    codes per email per purpose in WINDOW_MINUTES -- so a 6-digit space cannot
    be brute-forced or used to flood someone's inbox.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailCode
from app.security import SECRET_KEY

PURPOSES = ("login", "verify", "reset")
CODE_MINUTES = 10
MAX_ATTEMPTS = 5
MAX_PER_WINDOW = 3
WINDOW_MINUTES = 10


class TooManyCodes(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    # SQLite hands datetimes back naive; they were written in UTC.
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _hash(email: str, purpose: str, code: str) -> str:
    return hmac.new(SECRET_KEY.encode(), f"{email.lower()}|{purpose}|{code}".encode(),
                    hashlib.sha256).hexdigest()


def issue(db: Session, email: str, purpose: str) -> str:
    """A fresh code for (email, purpose). Raises TooManyCodes past the rate limit."""
    email = email.lower()
    since = _now() - timedelta(minutes=WINDOW_MINUTES)
    recent = [c for c in db.scalars(select(EmailCode).where(
        EmailCode.email == email, EmailCode.purpose == purpose)).all()
        if _aware(c.created_at) >= since]
    if len(recent) >= MAX_PER_WINDOW:
        raise TooManyCodes()
    for old in recent:                     # only the newest code works
        if old.used_at is None:
            old.used_at = _now()
    code = f"{secrets.randbelow(10**6):06d}"
    db.add(EmailCode(email=email, purpose=purpose, code_hash=_hash(email, purpose, code),
                     expires_at=_now() + timedelta(minutes=CODE_MINUTES)))
    return code


def check(db: Session, email: str, purpose: str, code: str) -> bool:
    """True once, for the right code in time. Every wrong guess counts."""
    email = email.lower()
    row = db.scalars(select(EmailCode).where(
        EmailCode.email == email, EmailCode.purpose == purpose, EmailCode.used_at.is_(None))
        .order_by(EmailCode.created_at.desc())).first()
    if row is None or _aware(row.expires_at) < _now() or row.attempts >= MAX_ATTEMPTS:
        return False
    row.attempts += 1
    ok = hmac.compare_digest(row.code_hash, _hash(email, purpose, code.strip()))
    if ok:
        row.used_at = _now()
    return ok
