"""Sign up, sign in, sign out, who-am-I, switch business.

Email + password for now. Google sign-in and email OTP are later additions that
end in the same issue_session() call.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import current_user
from app.models import Membership, Tenant, User
from app.security import (SESSION_COOKIE, SESSION_DAYS, hash_password, issue_session,
                          read_session, verify_password)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    business_name: str = Field(min_length=2, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class SwitchIn(BaseModel):
    tenant_id: str


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=SESSION_DAYS * 86400, path="/")


def _me(db: Session, user: User, active_tenant_id: str | None) -> dict:
    memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    businesses = [{"id": m.tenant.id, "name": m.tenant.legal_name, "role": m.role}
                  for m in memberships]
    active = next((b for b in businesses if b["id"] == active_tenant_id), None)
    return {"user": {"id": user.id, "name": user.name, "email": user.email},
            "businesses": businesses, "active_business": active}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    tenant = Tenant(legal_name=body.business_name.strip(), contact_name=body.name.strip(),
                    contact_email=email)
    db.add_all([user, tenant])
    db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
    audit.record(db, tenant_id=tenant.id, actor=email, action="business_created",
                 reason=f"{body.name.strip()} created {tenant.legal_name} and is its owner")
    db.commit()
    _set_cookie(response, issue_session(user.id, tenant.id))
    return _me(db, user, tenant.id)


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email or password is incorrect")
    first = db.scalar(select(Membership).where(Membership.user_id == user.id))
    tenant_id = first.tenant_id if first else None
    _set_cookie(response, issue_session(user.id, tenant_id))
    return _me(db, user, tenant_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/session")
def session_info(db: Session = Depends(get_db), user: User = Depends(current_user),
                 session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict:
    """Who am I, which businesses can I use, which one is active -- what the
    web app calls on load."""
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
