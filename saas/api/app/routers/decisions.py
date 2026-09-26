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

from app import audit, bridge, mailer, payments
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
                  "buyer_email": inv.buyer.contact_email, "buyer_opted_out": inv.buyer.opted_out,
                  "payments_enabled": payments.enabled(),
                  "contacted_today": any(c.contacted_on == today for c in inv.contacts),
                  "score": {"value": score["score"], "confidence": score["confidence"],
                            "breakdown": score["breakdown"]}}


class ApproveIn(BaseModel):
    channel: str = "manual"


def _decide_now(ctx: TenantContext, inv: Invoice, today: date) -> tuple[dict, dict]:
    """Today's decision, re-computed at the moment of acting -- a stale screen
    can never send or approve what the rules no longer allow."""
    invoices = ctx.db.scalars(ctx.scoped(Invoice).where(Invoice.buyer_id == inv.buyer_id)).all()
    score = bridge.score_buyers([inv.buyer], invoices, today)[inv.buyer_id]
    with bridge.as_tenant(ctx.tenant):
        decision = bridge.decide(inv, inv.buyer, score, today)
    if decision["kind"] not in ("send", "payment_plan", "counter_settle"):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Today's decision is {decision['kind']}, not a message: "
                            f"{decision['reason']}")
    if any(c.contacted_on == today for c in inv.contacts):
        raise HTTPException(status.HTTP_409_CONFLICT, "This buyer was already contacted about "
                                                      "this invoice today")
    return decision, score


@router.post("/{invoice_id}/send-email", status_code=status.HTTP_201_CREATED)
def send_email(invoice_id: str, ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Email today's reminder to the buyer -- the writer's guardrailed draft,
    regenerated now, never free text -- after the rules re-decide it. Logged as
    a contact only once the email is actually accepted for delivery."""
    today = today_for()
    inv = ctx.get(Invoice, invoice_id)
    buyer = inv.buyer
    if buyer.opted_out:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{buyer.name} opted out of reminders")
    if not buyer.contact_email:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Add an email address for {buyer.name} first")
    decision, score = _decide_now(ctx, inv, today)
    with bridge.as_tenant(ctx.tenant):
        draft = bridge.draft_message(inv, buyer, score, decision["skeleton"], today)

    link = None
    if payments.enabled():
        try:
            link = payments.link_for(ctx.db, inv, ctx.tenant, today, ctx.user.email)
        except payments.PaymentsError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't make the payment link: {exc}")
    reply_to = ctx.tenant.contact_email or ctx.user.email
    body = draft["body"].rstrip()
    if link:
        body += f"\n\nPay online (UPI, card or netbanking): {link.short_url}"
    body += (f"\n\n--\nSent on behalf of {ctx.tenant.legal_name} by Recova. "
             f"Reply to this email to reach {ctx.tenant.legal_name} directly.\n")
    row = mailer.queue(ctx.db, to=buyer.contact_email, kind="buyer_reminder",
                       subject=draft["subject"] or f"Invoice {inv.invoice_number}",
                       text=body, tenant_id=ctx.tenant.id,
                       from_name=f"{ctx.tenant.legal_name} via Recova", reply_to=reply_to)
    outcome = mailer.send_now(ctx.db, row)
    if outcome != "sent":
        ctx.db.commit()          # keep the outbox row (blocked/failed) for the record
        why = ("held back by RECOVA_EMAIL_ALLOWLIST -- this server only emails listed addresses"
               if outcome == "blocked" else f"the mail server refused it: {row.last_error}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Not sent: {why}. Nothing was logged.")

    ctx.db.add(ContactLog(tenant_id=ctx.tenant.id, invoice_id=inv.id, contacted_on=today,
                          rung=decision["rung"], channel="email"))
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="reminder_emailed",
                 reason=f"rung {decision['rung']} ({decision['rung_name']}) reminder emailed to "
                        f"{buyer.contact_email}, approved by {ctx.user.email} -- {decision['reason']}",
                 source="rule", invoice_number=inv.invoice_number, buyer_name=buyer.name,
                 detail={"rung": decision["rung"], "to": buyer.contact_email, "outbox": row.id,
                         "subject": draft["subject"], "payment_link": link.short_url if link else None,
                         "draft_source": draft.get("source")})
    ctx.db.commit()
    return {"ok": True, "to": buyer.contact_email, "rung": decision["rung"],
            "payment_link": link.short_url if link else None}


@router.post("/{invoice_id}/approve", status_code=status.HTTP_201_CREATED)
def approve(invoice_id: str, body: ApproveIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    """The owner sent today's reminder themselves. Re-decides first, so a stale
    screen can never approve something the rules no longer allow."""
    today = today_for()
    inv = ctx.get(Invoice, invoice_id)
    decision, _score = _decide_now(ctx, inv, today)
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
