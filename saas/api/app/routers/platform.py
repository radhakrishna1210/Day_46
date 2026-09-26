"""The platform area: for whoever runs Recova itself, not for any one business.

Read-only, and deliberately shallow: per business it shows who is in it and
how much it holds (counts), never a buyer, an invoice amount or a message.
Access is RECOVA_SUPER_ADMIN_EMAILS + a verified email (app/deps.py); everyone
else gets a 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
        "is_super_admin": is_super_admin(u),
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
