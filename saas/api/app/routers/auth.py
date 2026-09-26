"""Accounts and sessions.

Three ways in, all ending in the same issue_session():
  * email + password           POST /auth/login
  * a one-time email code      POST /auth/code/request -> /auth/code/verify
  * Google                     GET  /auth/google/start -> /auth/google/callback

Codes and verification emails go through the outbox (app/mailer.py) to the
person signing in -- never to anyone else.
"""

from __future__ import annotations

import secrets
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, google, mailer, otp, settings
from app.db import get_db
from app.deps import SUSPENDED_USER, current_user, is_super_admin
from app.routers import team
from app.models import Membership, Tenant, User
from app.security import (SESSION_COOKIE, SESSION_DAYS, hash_password, issue_session,
                          read_session, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_STATE_COOKIE = "recova_oauth"
GOOGLE_NEXT_COOKIE = "recova_next"      # where to land after Google (e.g. an invite page)
GENERIC_CODE_REPLY = {"ok": True, "message": "If that email has an account, a code is on its way."}


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    #: Required unless joining through an invitation (invite_token).
    business_name: str | None = Field(default=None, min_length=2, max_length=200)
    invite_token: str | None = Field(default=None, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class SwitchIn(BaseModel):
    tenant_id: str


class CodeRequestIn(BaseModel):
    email: EmailStr
    purpose: Literal["login", "reset"] = "login"


class CodeVerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResetIn(CodeVerifyIn):
    new_password: str = Field(min_length=8, max_length=200)


class VerifyEmailIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=settings.public_url().startswith("https://"),
                        max_age=SESSION_DAYS * 86400, path="/")


def _first_tenant_id(db: Session, user: User) -> str | None:
    first = db.scalar(select(Membership).where(Membership.user_id == user.id))
    return first.tenant_id if first else None


def _me(db: Session, user: User, active_tenant_id: str | None) -> dict:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    businesses = [{"id": m.tenant.id, "name": m.tenant.legal_name, "role": m.role}
                  for m in memberships]
    active = next((b for b in businesses if b["id"] == active_tenant_id), None)
    return {"user": {"id": user.id, "name": user.name, "email": user.email,
                     "email_verified": user.email_verified,
                     "has_password": user.password_hash is not None,
                     "google_linked": user.google_sub is not None,
                     "is_super_admin": is_super_admin(user)},
            "businesses": businesses, "active_business": active}


def _user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == email.lower()))


def _send_code(db: Session, email: str, purpose: str) -> None:
    """Issue a code and email it. Raises 429 past the per-email rate limit."""
    try:
        code = otp.issue(db, email, purpose)
    except otp.TooManyCodes:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Too many codes requested. Try again in {otp.WINDOW_MINUTES} minutes.")
    subject, text, html_body = mailer.code_email(purpose, code, otp.CODE_MINUTES)
    mailer.queue(db, to=email.lower(), kind=f"{purpose}_code", subject=subject, text=text,
                 html_body=html_body)


def _sign_in(db: Session, response: Response, user: User) -> dict:
    if user.suspended_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, SUSPENDED_USER)
    tenant_id = _first_tenant_id(db, user)
    _set_cookie(response, issue_session(user.id, tenant_id))
    return _me(db, user, tenant_id)


# --------------------------------------------------------------------------
# password accounts
# --------------------------------------------------------------------------

@router.get("/providers")
def providers() -> dict:
    """Which sign-in methods this server offers -- the sign-in page asks."""
    return {"password": True, "email_code": True, "google": settings.google_enabled(),
            "email_delivery": "smtp" if settings.smtp_enabled() else "log"}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    if _user_by_email(db, email):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))

    if body.invite_token:
        # Joining someone's business: no business of their own, and the emailed
        # link already proves the inbox, so no verification code either.
        invite = team.find_invite(db, body.invite_token)
        if invite is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This invitation link isn't valid")
        db.add(user)
        db.flush()
        membership = team.accept_invite(db, invite, user)
        db.commit()
        _set_cookie(response, issue_session(user.id, membership.tenant_id))
        return _me(db, user, membership.tenant_id)

    if not body.business_name or not body.business_name.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Business name is required")
    tenant = Tenant(legal_name=body.business_name.strip(), contact_name=body.name.strip(),
                    contact_email=email)
    db.add_all([user, tenant])
    db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
    audit.record(db, tenant_id=tenant.id, actor=email, action="business_created",
                 reason=f"{body.name.strip()} created {tenant.legal_name} and is its owner")
    _send_code(db, email, "verify")
    db.commit()
    mailer.deliver_pending(db)
    _set_cookie(response, issue_session(user.id, tenant.id))
    return _me(db, user, tenant.id)


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> dict:
    user = _user_by_email(db, body.email)
    if user is None or user.password_hash is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    return _sign_in(db, response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/session")
def session_info(db: Session = Depends(get_db), user: User = Depends(current_user),
                 session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    """Who am I, which businesses can I use, which one is active."""
    claims = read_session(session) if session else None
    return _me(db, user, (claims or {}).get("tid"))


@router.post("/switch")
def switch(body: SwitchIn, response: Response, db: Session = Depends(get_db),
           user: User = Depends(current_user)) -> dict:
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.tenant_id == body.tenant_id))
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found")
    _set_cookie(response, issue_session(user.id, body.tenant_id))
    return _me(db, user, body.tenant_id)


# --------------------------------------------------------------------------
# one-time email codes
# --------------------------------------------------------------------------

@router.post("/code/request", status_code=status.HTTP_202_ACCEPTED)
def request_code(body: CodeRequestIn, db: Session = Depends(get_db)) -> dict:
    """Email a sign-in (or password-reset) code. The reply is the same whether
    or not the email has an account, so this cannot be used to find accounts."""
    if _user_by_email(db, body.email) is not None:
        _send_code(db, body.email, body.purpose)
        db.commit()
        mailer.deliver_pending(db)
    return GENERIC_CODE_REPLY


@router.post("/code/verify")
def verify_code(body: CodeVerifyIn, response: Response, db: Session = Depends(get_db)) -> dict:
    user = _user_by_email(db, body.email)
    ok = user is not None and otp.check(db, body.email, "login", body.code)
    db.commit()                                   # a wrong guess still counts
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is wrong or has expired")
    user.email_verified = True                    # they just proved they read that inbox
    db.commit()
    return _sign_in(db, response, user)


@router.post("/password/reset")
def reset_password(body: ResetIn, response: Response, db: Session = Depends(get_db)) -> dict:
    user = _user_by_email(db, body.email)
    ok = user is not None and otp.check(db, body.email, "reset", body.code)
    db.commit()
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is wrong or has expired")
    user.password_hash = hash_password(body.new_password)
    user.email_verified = True
    db.commit()
    return _sign_in(db, response, user)


@router.post("/verify-email/send", status_code=status.HTTP_202_ACCEPTED)
def send_verification(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    if user.email_verified:
        return {"ok": True, "message": "Your email is already confirmed."}
    _send_code(db, user.email, "verify")
    db.commit()
    mailer.deliver_pending(db)
    return {"ok": True, "message": f"A code is on its way to {user.email}."}


@router.post("/verify-email")
def verify_email(body: VerifyEmailIn, db: Session = Depends(get_db),
                 user: User = Depends(current_user)) -> dict:
    ok = otp.check(db, user.email, "verify", body.code)
    db.commit()
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code is wrong or has expired")
    user.email_verified = True
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------

def _signin_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.public_url()}/login?error={quote(message)}", status_code=302)


def _safe_next(path: str | None) -> str | None:
    """Only our own pages -- never an open redirect to another site."""
    if path and path.startswith(("/app", "/invite/")) and "//" not in path and "\\" not in path:
        return path
    return None


@router.get("/google/start")
def google_start(next: str | None = None) -> RedirectResponse:  # noqa: A002 -- the query name
    if not settings.google_enabled():
        return _signin_error("Google sign-in is not set up on this server")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    resp = RedirectResponse(google.authorization_url(state, nonce), status_code=302)
    secure = settings.public_url().startswith("https://")
    resp.set_cookie(GOOGLE_STATE_COOKIE, f"{state}.{nonce}", httponly=True, samesite="lax",
                    secure=secure, max_age=600, path="/")
    if _safe_next(next):
        resp.set_cookie(GOOGLE_NEXT_COOKIE, next, httponly=True, samesite="lax",
                        secure=secure, max_age=600, path="/")
    return resp


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db),
                    oauth: str | None = Cookie(default=None, alias=GOOGLE_STATE_COOKIE),
                    next_path: str | None = Cookie(default=None, alias=GOOGLE_NEXT_COOKIE)
                    ) -> RedirectResponse:
    if error:
        return _signin_error("Google sign-in was cancelled")
    expected_state, _, nonce = (oauth or "").partition(".")
    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return _signin_error("Google sign-in expired -- please try again")
    try:
        claims = google.exchange_code(code, nonce)
    except Exception:  # noqa: BLE001 -- never show token internals to the browser
        return _signin_error("Google sign-in failed -- please try again")
    if not claims.get("email") or not claims.get("email_verified"):
        return _signin_error("Your Google account has no verified email address")

    email, sub = claims["email"].lower(), claims["sub"]
    user = db.scalar(select(User).where(User.google_sub == sub)) or _user_by_email(db, email)
    if user is None:
        user = User(email=email, name=(claims.get("name") or email.split("@")[0])[:120],
                    password_hash=None, email_verified=True, google_sub=sub)
        db.add(user)
    else:
        # Linking by email is safe: Google has verified this address, and a
        # password account at the same address is the same person's inbox.
        user.google_sub = user.google_sub or sub
        user.email_verified = True
    db.commit()
    if user.suspended_at is not None:
        return _signin_error(SUSPENDED_USER)

    tenant_id = _first_tenant_id(db, user)
    # Back to where they started (an invite page), else: no business yet ->
    # name one, unless a platform super admin, who needs none.
    dest = _safe_next(next_path) or (
        "/app" if tenant_id else "/app/platform" if is_super_admin(user) else "/welcome")
    resp = RedirectResponse(f"{settings.public_url()}{dest}", status_code=302)
    _set_cookie(resp, issue_session(user.id, tenant_id))
    resp.delete_cookie(GOOGLE_STATE_COOKIE, path="/")
    resp.delete_cookie(GOOGLE_NEXT_COOKIE, path="/")
    return resp
