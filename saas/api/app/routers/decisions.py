"""Today's decision queue -- what the rules say to do about every open invoice.

The agent DECIDES; it does not send. Channels (email / WhatsApp / payments) are
not connected yet, so "approve" records that the owner sent the reminder
themselves, which the engine needs as history to pace the next one. Every
approval is an audit row.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, bridge
from app.clock import today_for
from app.deps import TenantContext, tenant_context
from app.models import Buyer, ContactLog, Invoice, Tenant

router = APIRouter(prefix="/decisions", tags=["decisions"])

KIND_ORDER = {"send": 0, "payment_plan": 0, "counter_settle": 0, "handoff": 1, "wait": 2,
              "stop": 3}


def _decision_row(inv: Invoice, decision: dict, legal: dict, owed: int) -> dict:
    return {
        "invoice_id": inv.id, "invoice_number": inv.invoice_number,
        "buyer": {"id": inv.buyer.id, "name": inv.buyer.name, "profile": inv.buyer.profile,
                  "language_pref": inv.buyer.language_pref},
        "outstanding_paise": owed, "days_overdue": legal["days_overdue"],
        "interest_paise": legal["interest_paise"],
        **{k: v for k, v in decision.items() if k != "skeleton"},
    }


@router.get("")
def queue(ctx: TenantContext = Depends(tenant_context), as_of: date | None = None) -> dict:
    return build_queue(ctx.db, ctx.tenant, today_for(as_of))


def build_queue(db: Session, tenant: Tenant, today: date) -> dict:
    """The queue for one business on one day. No request needed, so the daily
    run (app/digest.py) computes exactly what the Decisions page shows."""
    buyers = db.scalars(select(Buyer).where(Buyer.tenant_id == tenant.id)).all()
    invoices = db.scalars(select(Invoice).where(Invoice.tenant_id == tenant.id)).all()
    scores = bridge.score_buyers(buyers, invoices, today)
    rows = []
    with bridge.as_tenant(tenant):
        for inv in invoices:
            owed = bridge.outstanding(inv, today)
            if owed <= 0:
                continue
            legal = bridge.legal_summary(inv, today)
            if legal["days_overdue"] <= 0 and not inv.disputed:
                continue          # not due yet -- nothing to decide
            decision = bridge.decide(inv, inv.buyer, scores[inv.buyer_id], today)
            rows.append(_decision_row(inv, decision, legal, owed))
    rows.sort(key=lambda r: (KIND_ORDER.get(r["kind"], 9), -r["outstanding_paise"]))
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
    return {"as_of": today.isoformat(), "tally": tally, "decisions": rows}


@router.get("/{invoice_id}")
def one(invoice_id: str, ctx: TenantContext = Depends(tenant_context),
        as_of: date | None = None) -> dict:
    """One decision in full, with the draft message the writer would use."""
    today = today_for(as_of)
    inv = ctx.get(Invoice, invoice_id)
    invoices = ctx.db.scalars(ctx.scoped(Invoice).where(Invoice.buyer_id == inv.buyer_id)).all()
    score = bridge.score_buyers([inv.buyer], invoices, today)[inv.buyer_id]
    legal = bridge.legal_summary(inv, today)
    with bridge.as_tenant(ctx.tenant):
        decision = bridge.decide(inv, inv.buyer, score, today)
        draft = (bridge.draft_message(inv, inv.buyer, score, decision["skeleton"], today)
                 if decision["skeleton"] is not None else None)
    row = _decision_row(inv, decision, legal, bridge.outstanding(inv, today))
    return row | {"draft": draft, "legal": legal,
                  "score": {"value": score["score"], "confidence": score["confidence"],
                            "breakdown": score["breakdown"]}}


class ApproveIn(BaseModel):
    channel: str = "manual"


@router.post("/{invoice_id}/approve", status_code=status.HTTP_201_CREATED)
def approve(invoice_id: str, body: ApproveIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    """The owner sent today's reminder themselves. Re-decides first, so a stale
    screen can never approve something the rules no longer allow."""
    today = today_for()
    inv = ctx.get(Invoice, invoice_id)
    invoices = ctx.db.scalars(ctx.scoped(Invoice).where(Invoice.buyer_id == inv.buyer_id)).all()
    score = bridge.score_buyers([inv.buyer], invoices, today)[inv.buyer_id]
    with bridge.as_tenant(ctx.tenant):
        decision = bridge.decide(inv, inv.buyer, score, today)
    if decision["kind"] not in ("send", "payment_plan", "counter_settle"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Today's decision is {decision['kind']}, not a message: "
                            f"{decision['reason']}")
    ctx.db.add(ContactLog(tenant_id=ctx.tenant.id, invoice_id=inv.id, contacted_on=today,
                          rung=decision["rung"], channel=body.channel))
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email,
                 action="reminder_approved",
                 reason=f"rung {decision['rung']} ({decision['rung_name']}) reminder approved "
                        f"and sent by the owner via {body.channel} -- {decision['reason']}",
                 source="rule", invoice_number=inv.invoice_number, buyer_name=inv.buyer.name,
                 detail={"rung": decision["rung"], "channel": body.channel})
    ctx.db.commit()
    return {"ok": True, "rung": decision["rung"]}
