"""The audit trail, read-only. There is no endpoint that edits or deletes it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.deps import TenantContext, tenant_context
from app.models import AuditEntry

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def entries(ctx: TenantContext = Depends(tenant_context), action: str | None = None,
            q: str | None = None, limit: int = Query(100, le=500), offset: int = 0) -> dict:
    query = ctx.scoped(AuditEntry).order_by(AuditEntry.at.desc())
    if action:
        query = query.where(AuditEntry.action == action)
    if q:
        like = f"%{q}%"
        query = query.where(AuditEntry.reason.ilike(like) | AuditEntry.invoice_number.ilike(like)
                            | AuditEntry.buyer_name.ilike(like))
    rows = ctx.db.scalars(query.offset(offset).limit(limit + 1)).all()
    return {
        "entries": [{"id": e.id, "at": e.at.isoformat(), "actor": e.actor, "action": e.action,
                     "invoice_number": e.invoice_number, "buyer_name": e.buyer_name,
                     "reason": e.reason, "source": e.source, "detail": e.detail}
                    for e in rows[:limit]],
        "has_more": len(rows) > limit,
    }
