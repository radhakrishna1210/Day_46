"""Append-only audit trail, per tenant -- non-negotiable #1 carried into the
SaaS: every money-related action, with who, what, why and whether a rule, the
AI or a person decided it."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEntry


def record(db: Session, *, tenant_id: str, actor: str, action: str, reason: str,
           source: str = "user", invoice_number: str | None = None,
           buyer_name: str | None = None, detail: dict[str, Any] | None = None) -> AuditEntry:
    entry = AuditEntry(tenant_id=tenant_id, actor=actor, action=action, reason=reason,
                       source=source, invoice_number=invoice_number, buyer_name=buyer_name,
                       detail=detail or {})
    db.add(entry)
    return entry
