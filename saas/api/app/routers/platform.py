"""The platform area: for whoever runs Recova itself, not for any one business.

Deliberately shallow: per business it shows who is in it and how much it
holds (counts), never a buyer, an invoice amount or a message. The one power
it has is to suspend (and reactivate) a business or a user -- recorded in the
affected businesses' own audit trails, so their owners can see it happened.
Access is RECOVA_SUPER_ADMIN_EMAILS + a verified email (app/deps.py); everyone
else gets a 404.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.db import get_db
from app.deps import is_super_admin, super_admin
from app.models import AuditEntry, Buyer, Invoice, Membership, OutboxEmail, Tenant, User

router = APIRouter(prefix="/platform", tags=["platform"])


def _counts(db: Session, column) -> dict[str, int]:
    """tenant_id -> row count, one grouped query per table."""
    return dict(db.execute(select(column, func.count()).group_by(column)).all())


def _iso(value) -> str | None:
    return value.isoformat() if value else None


@router.get("/overview")
def overview(db: Session = Depends(get_db), _admin: User = Depends(super_admin)) -> dict:
    buyers = _counts(db, Buyer.tenant_id)
    invoices = _counts(db, Invoice.tenant_id)
    members = _counts(db, Membership.tenant_id)
    last_activity = dict(db.execute(
        select(AuditEntry.tenant_id, func.max(AuditEntry.at)).group_by(AuditEntry.tenant_id)).all())
    owners: dict[str, str] = {}
    for m in db.scalars(select(Membership).where(Membership.role == "owner")
                        .order_by(Membership.id)).all():
        owners.setdefault(m.tenant_id, m.user.email)

    businesses = [{
        "id": t.id, "name": t.legal_name, "created_at": _iso(t.created_at),
        "suspended_at": _iso(t.suspended_at),
        "owner_email": owners.get(t.id), "members": members.get(t.id, 0),
        "buyers": buyers.get(t.id, 0), "invoices": invoices.get(t.id, 0),
        "udyam_registered": bool(t.udyam_registration),
        "last_activity": _iso(last_activity.get(t.id)),
    } for t in db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()]

    per_user = _counts(db, Membership.user_id)
    users = [{
        "id": u.id, "name": u.name, "email": u.email, "created_at": _iso(u.created_at),
        "email_verified": u.email_verified, "has_password": u.password_hash is not None,
        "google_linked": u.google_sub is not None, "businesses": per_user.get(u.id, 0),
        "is_super_admin": is_super_admin(u), "suspended_at": _iso(u.suspended_at),
    } for u in db.scalars(select(User).order_by(User.created_at.desc())).all()]

    email_status = dict(db.execute(
        select(OutboxEmail.status, func.count()).group_by(OutboxEmail.status)).all())
    failed = db.scalars(select(OutboxEmail).where(OutboxEmail.status == "failed")
                        .order_by(OutboxEmail.created_at.desc()).limit(10)).all()

    return {
        "totals": {"businesses": len(businesses), "users": len(users),
                   "buyers": sum(buyers.values()), "invoices": sum(invoices.values())},
        "businesses": businesses,
        "users": users,
        "email": {"sent": email_status.get("sent", 0), "queued": email_status.get("queued", 0),
                  "failed": email_status.get("failed", 0),
                  "recent_failures": [{"to": e.to_email, "kind": e.kind, "at": _iso(e.created_at),
                                       "error": (e.last_error or "")[:200]} for e in failed]},
    }


# --------------------------------------------------------------------------
# suspend / reactivate
# --------------------------------------------------------------------------

class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=300)


def _why(body: ReasonIn | None) -> str:
    text = (body.reason if body else "").strip()
    return f" -- {text}" if text else ""


@router.post("/businesses/{tenant_id}/suspend")
def suspend_business(tenant_id: str, body: ReasonIn | None = None, db: Session = Depends(get_db),
                     admin: User = Depends(super_admin)) -> dict:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found")
    if tenant.suspended_at is None:
        tenant.suspended_at = datetime.now(timezone.utc)
        audit.record(db, tenant_id=tenant.id, actor=f"platform:{admin.email}",
                     action="business_suspended",
                     reason=f"Recova platform admin suspended this business{_why(body)}")
        db.commit()
    return {"id": tenant.id, "suspended_at": _iso(tenant.suspended_at)}


@router.post("/businesses/{tenant_id}/reactivate")
def reactivate_business(tenant_id: str, db: Session = Depends(get_db),
                        admin: User = Depends(super_admin)) -> dict:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found")
    if tenant.suspended_at is not None:
        tenant.suspended_at = None
        audit.record(db, tenant_id=tenant.id, actor=f"platform:{admin.email}",
                     action="business_reactivated",
                     reason="Recova platform admin reactivated this business")
        db.commit()
    return {"id": tenant.id, "suspended_at": None}


def _user_for_admin(db: Session, user_id: str, admin: User) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id or is_super_admin(user):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "A super admin can't be suspended from here -- remove them from "
                            "RECOVA_SUPER_ADMIN_EMAILS first")
    return user


def _note_on_their_businesses(db: Session, user: User, admin: User, action: str,
                              reason: str) -> None:
    for tenant_id in db.scalars(select(Membership.tenant_id).where(Membership.user_id == user.id)):
        audit.record(db, tenant_id=tenant_id, actor=f"platform:{admin.email}", action=action,
                     reason=reason)


@router.post("/users/{user_id}/suspend")
def suspend_user(user_id: str, body: ReasonIn | None = None, db: Session = Depends(get_db),
                 admin: User = Depends(super_admin)) -> dict:
    user = _user_for_admin(db, user_id, admin)
    if user.suspended_at is None:
        user.suspended_at = datetime.now(timezone.utc)
        _note_on_their_businesses(db, user, admin, "user_suspended",
                                  f"Recova platform admin suspended {user.email}{_why(body)}")
        db.commit()
    return {"id": user.id, "suspended_at": _iso(user.suspended_at)}


@router.post("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, db: Session = Depends(get_db),
                    admin: User = Depends(super_admin)) -> dict:
    user = _user_for_admin(db, user_id, admin)
    if user.suspended_at is not None:
        user.suspended_at = None
        _note_on_their_businesses(db, user, admin, "user_reactivated",
                                  f"Recova platform admin reactivated {user.email}")
        db.commit()
    return {"id": user.id, "suspended_at": None}
