"""Request context: who is calling, and for which business.

Every tenant-owned query in the API goes through TenantContext.scoped(), which
adds `WHERE tenant_id = <caller's active tenant>`. There is no other path to a
tenant's rows, so one business can never read or write another's --
tests/test_tenancy.py tries, and must fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Membership, Tenant, User
from app.security import SESSION_COOKIE, read_session


def current_user(db: Session = Depends(get_db),
                 session: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> User:
    claims = read_session(session) if session else None
    user = db.get(User, claims["sub"]) if claims else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


@dataclass
class TenantContext:
    db: Session
    user: User
    tenant: Tenant
    role: str

    def scoped(self, model: Any):
        """SELECT model WHERE tenant_id = this tenant. The only way in."""
        return select(model).where(model.tenant_id == self.tenant.id)

    def get(self, model: Any, row_id: str):
        """One row by id, or 404 -- including when it exists but belongs to
        another tenant (never reveal that it exists)."""
        row = self.db.get(model, row_id)
        if row is None or row.tenant_id != self.tenant.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
        return row

    def require(self, *roles: str) -> None:
        if self.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot do this")


def tenant_context(db: Session = Depends(get_db), user: User = Depends(current_user),
                   session: str | None = Cookie(default=None, alias=SESSION_COOKIE)
                   ) -> TenantContext:
    claims = read_session(session) or {}
    tenant_id = claims.get("tid")
    membership = db.scalar(select(Membership).where(
        Membership.user_id == user.id, Membership.tenant_id == tenant_id)) if tenant_id else None
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No active business for this session")
    return TenantContext(db=db, user=user, tenant=membership.tenant, role=membership.role)
