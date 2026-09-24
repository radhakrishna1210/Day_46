"""Buyers -- the businesses that owe this tenant money."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app import audit, bridge
from app.clock import today_for
from app.deps import TenantContext, tenant_context
from app.models import Buyer, Invoice

router = APIRouter(prefix="/buyers", tags=["buyers"])


class BuyerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    profile: str = Field(default="corporate", pattern="^(corporate|small_trader)$")
    sector: str | None = None
    language_pref: str = Field(default="english", pattern="^(english|hinglish)$")
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    city: str | None = None
    state: str | None = None
    gstin: str | None = None
    preferred_channel: str = Field(default="email", pattern="^(email|whatsapp|sms)$")
    opted_out: bool = False


def buyer_out(b: Buyer, score: dict | None = None, totals: dict | None = None) -> dict:
    return {
        "id": b.id, "code": b.code, "name": b.name, "profile": b.profile, "sector": b.sector,
        "language_pref": b.language_pref, "contact_name": b.contact_name,
        "contact_email": b.contact_email, "contact_phone": b.contact_phone, "city": b.city,
        "state": b.state, "gstin": b.gstin, "preferred_channel": b.preferred_channel,
        "opted_out": b.opted_out,
        "score": None if score is None else {
            "value": score["score"], "confidence": score["confidence"],
            "history_count": score["history_count"],
            "trend": score["trend"].get("direction") if isinstance(score.get("trend"), dict) else None,
            "breakdown": score["breakdown"],
        },
        **(totals or {}),
    }


def _next_code(ctx: TenantContext) -> str:
    count = len(ctx.db.scalars(ctx.scoped(Buyer)).all())
    return f"BUY-{count + 1:03d}"


@router.get("")
def list_buyers(ctx: TenantContext = Depends(tenant_context), as_of: date | None = None) -> list[dict]:
    today = today_for(as_of)
    buyers = ctx.db.scalars(ctx.scoped(Buyer).order_by(Buyer.name)).all()
    invoices = ctx.db.scalars(ctx.scoped(Invoice)).all()
    scores = bridge.score_buyers(buyers, invoices, today)
    out = []
    for b in buyers:
        mine = [i for i in invoices if i.buyer_id == b.id]
        owed = sum(bridge.outstanding(i, today) for i in mine)
        overdue = [i for i in mine if bridge.outstanding(i, today) > 0
                   and bridge.legal_summary(i, today)["days_overdue"] > 0]
        out.append(buyer_out(b, scores[b.id], {
            "invoice_count": len(mine), "outstanding_paise": owed,
            "overdue_count": len(overdue),
            "overdue_paise": sum(bridge.outstanding(i, today) for i in overdue),
        }))
    return out


@router.post("", status_code=status.HTTP_201_CREATED)
def create_buyer(body: BuyerIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    code = (body.code or _next_code(ctx)).strip()
    if ctx.db.scalars(ctx.scoped(Buyer).where(Buyer.code == code)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"A buyer with code {code} already exists")
    buyer = Buyer(tenant_id=ctx.tenant.id, **body.model_dump(exclude={"code"}), code=code)
    ctx.db.add(buyer)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="buyer_added",
                 reason=f"added buyer {buyer.name} ({code})", buyer_name=buyer.name)
    ctx.db.commit()
    return buyer_out(buyer)


@router.get("/{buyer_id}")
def get_buyer(buyer_id: str, ctx: TenantContext = Depends(tenant_context),
              as_of: date | None = None) -> dict:
    today = today_for(as_of)
    buyer = ctx.get(Buyer, buyer_id)
    invoices = ctx.db.scalars(ctx.scoped(Invoice).where(Invoice.buyer_id == buyer.id)).all()
    score = bridge.score_buyers([buyer], invoices, today)[buyer.id]
    return buyer_out(buyer, score, {
        "invoices": [{"id": i.id, "invoice_number": i.invoice_number,
                      "amount_paise": i.amount_paise,
                      "outstanding_paise": bridge.outstanding(i, today),
                      "days_overdue": bridge.legal_summary(i, today)["days_overdue"]}
                     for i in invoices],
    })


@router.put("/{buyer_id}")
def update_buyer(buyer_id: str, body: BuyerIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    buyer = ctx.get(Buyer, buyer_id)
    before_opt_out = buyer.opted_out
    for key, value in body.model_dump(exclude={"code"}).items():
        setattr(buyer, key, value)
    if body.code:
        buyer.code = body.code.strip()
    reason = f"updated buyer {buyer.name}"
    if before_opt_out != buyer.opted_out:
        reason += "; opted OUT of all contact" if buyer.opted_out else "; opt-out lifted"
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="buyer_updated",
                 reason=reason, buyer_name=buyer.name)
    ctx.db.commit()
    return buyer_out(buyer)


@router.delete("/{buyer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_buyer(buyer_id: str, ctx: TenantContext = Depends(tenant_context)) -> None:
    ctx.require("owner", "admin")
    buyer = ctx.get(Buyer, buyer_id)
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="buyer_deleted",
                 reason=f"deleted buyer {buyer.name} and their invoices", buyer_name=buyer.name)
    ctx.db.delete(buyer)
    ctx.db.commit()
