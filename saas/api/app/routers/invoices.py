"""Invoices, and what happens to them: payments, promises, disputes, imports."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field, model_validator

from app import audit, bridge, payments, replies
from app.clock import today_for
from app.deps import TenantContext, tenant_context
from app.models import Buyer, BuyerReply, Invoice, Payment, Promise

router = APIRouter(prefix="/invoices", tags=["invoices"])


class InvoiceIn(BaseModel):
    buyer_id: str
    invoice_number: str = Field(min_length=1, max_length=60)
    description: str | None = None
    po_number: str | None = None
    amount_paise: int = Field(gt=0)
    issue_date: date
    acceptance_date: date
    written_agreement: bool = False
    agreed_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def _check(self):
        if self.acceptance_date < self.issue_date:
            raise ValueError("acceptance date cannot be before the issue date")
        if self.written_agreement and self.agreed_days is None:
            raise ValueError("a written agreement needs its agreed payment term in days")
        return self


class PaymentIn(BaseModel):
    paid_on: date
    amount_paise: int = Field(gt=0)
    note: str | None = None


class PromiseIn(BaseModel):
    promised_date: date
    amount: str = Field(default="full", pattern="^(full|partial)$")
    note: str | None = None


class DisputeIn(BaseModel):
    disputed: bool
    note: str | None = None


def invoice_out(inv: Invoice, today: date, *, detail: bool = False) -> dict:
    legal = bridge.legal_summary(inv, today)
    owed = bridge.outstanding(inv, today)
    status_ = ("paid" if owed <= 0 else "disputed" if inv.disputed
               else "overdue" if legal["days_overdue"] > 0 else "open")
    out = {
        "id": inv.id, "invoice_number": inv.invoice_number,
        "buyer": {"id": inv.buyer.id, "name": inv.buyer.name, "code": inv.buyer.code},
        "description": inv.description, "po_number": inv.po_number,
        "amount_paise": inv.amount_paise, "outstanding_paise": owed,
        "paid_paise": sum(p.amount_paise for p in inv.payments),
        "issue_date": inv.issue_date.isoformat(),
        "acceptance_date": inv.acceptance_date.isoformat(),
        "written_agreement": inv.written_agreement, "agreed_days": inv.agreed_days,
        "disputed": inv.disputed, "status": status_,
        "statutory_due_date": legal["statutory_due_date"],
        "days_overdue": legal["days_overdue"], "days_to_due": legal["days_to_due"],
        "interest_paise": legal["interest_paise"],
    }
    if detail:
        out |= {
            "dispute_note": inv.dispute_note, "legal": legal,
            "payments": [{"id": p.id, "paid_on": p.paid_on.isoformat(),
                          "amount_paise": p.amount_paise, "note": p.note} for p in inv.payments],
            "promises": [{"id": p.id, "promised_date": p.promised_date.isoformat(),
                          "amount": p.amount, "status": p.status,
                          "recorded_on": p.recorded_on.isoformat(), "note": p.note}
                         for p in inv.promises],
            "contacts": [{"id": c.id, "contacted_on": c.contacted_on.isoformat(), "rung": c.rung,
                          "rung_name": bridge.RUNG_NAMES.get(c.rung), "channel": c.channel,
                          "outcome": c.outcome} for c in inv.contacts],
            "payments_enabled": payments.enabled(),
            "payment_link": _link_out(inv),
            "replies": [{"id": r.id, "received_on": r.received_on.isoformat(), "channel": r.channel,
                         "text": r.text, "intent": r.intent, "suggested_intent": r.suggested_intent,
                         "suggested_by": r.suggested_by, "recorded_by": r.recorded_by,
                         "promised_date": r.promised_date.isoformat() if r.promised_date else None}
                        for r in inv.replies],
        }
    return out


def _link_out(inv: Invoice) -> dict | None:
    """The newest payment link, so the page shows paid / open / cancelled."""
    link = max(inv.payment_links, key=lambda l: l.created_at, default=None)
    if link is None:
        return None
    return {"url": link.short_url, "amount_paise": link.amount_paise, "status": link.status,
            "created_at": link.created_at.isoformat(), "mode": payments.mode()}


def _load(ctx: TenantContext, invoice_id: str) -> Invoice:
    return ctx.get(Invoice, invoice_id)


def _note(ctx: TenantContext, inv: Invoice, action: str, reason: str, **detail) -> None:
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action=action,
                 reason=reason, invoice_number=inv.invoice_number,
                 buyer_name=inv.buyer.name, detail=detail)


@router.get("")
def list_invoices(ctx: TenantContext = Depends(tenant_context), status_filter: str | None = None,
                  buyer_id: str | None = None, as_of: date | None = None) -> list[dict]:
    today = today_for(as_of)
    query = ctx.scoped(Invoice).order_by(Invoice.acceptance_date.desc())
    if buyer_id:
        query = query.where(Invoice.buyer_id == buyer_id)
    rows = [invoice_out(i, today) for i in ctx.db.scalars(query).all()]
    if status_filter:
        rows = [r for r in rows if r["status"] == status_filter]
    return rows


@router.post("", status_code=status.HTTP_201_CREATED)
def create_invoice(body: InvoiceIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    buyer = ctx.get(Buyer, body.buyer_id)
    if ctx.db.scalars(ctx.scoped(Invoice).where(
            Invoice.invoice_number == body.invoice_number)).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Invoice {body.invoice_number} already exists")
    inv = Invoice(tenant_id=ctx.tenant.id, **body.model_dump(exclude={"buyer_id"}),
                  buyer_id=buyer.id)
    ctx.db.add(inv)
    ctx.db.flush()
    _note(ctx, inv, "invoice_added", f"added invoice {inv.invoice_number} for {buyer.name}",
          amount_paise=inv.amount_paise)
    ctx.db.commit()
    return invoice_out(inv, today_for())


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str, ctx: TenantContext = Depends(tenant_context),
                as_of: date | None = None) -> dict:
    return invoice_out(_load(ctx, invoice_id), today_for(as_of), detail=True)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(invoice_id: str, ctx: TenantContext = Depends(tenant_context)) -> None:
    ctx.require("owner", "admin")
    inv = _load(ctx, invoice_id)
    _note(ctx, inv, "invoice_deleted", f"deleted invoice {inv.invoice_number}")
    ctx.db.delete(inv)
    ctx.db.commit()


@router.post("/{invoice_id}/payments", status_code=status.HTTP_201_CREATED)
def add_payment(invoice_id: str, body: PaymentIn,
                ctx: TenantContext = Depends(tenant_context)) -> dict:
    inv = _load(ctx, invoice_id)
    owed = bridge.outstanding(inv, body.paid_on)
    if body.amount_paise > owed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Payment is more than the {owed} paise still owed")
    ctx.db.add(Payment(tenant_id=ctx.tenant.id, invoice_id=inv.id, **body.model_dump()))
    for promise in inv.promises:        # money in on or before a promise keeps it
        if promise.status == "open" and body.paid_on <= promise.promised_date:
            promise.status = "kept"
    _note(ctx, inv, "payment_recorded",
          f"payment of {body.amount_paise} paise received on {body.paid_on}",
          amount_paise=body.amount_paise)
    ctx.db.commit()
    ctx.db.refresh(inv)
    return invoice_out(inv, today_for(), detail=True)


@router.post("/{invoice_id}/promises", status_code=status.HTTP_201_CREATED)
def add_promise(invoice_id: str, body: PromiseIn,
                ctx: TenantContext = Depends(tenant_context)) -> dict:
    inv = _load(ctx, invoice_id)
    today = today_for()
    if body.promised_date < today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A promise has to be for today or a future date")
    ctx.db.add(Promise(tenant_id=ctx.tenant.id, invoice_id=inv.id, recorded_on=today,
                       **body.model_dump()))
    _note(ctx, inv, "promise_recorded",
          f"buyer promised to pay ({body.amount}) by {body.promised_date}; "
          f"the agent holds off until then")
    ctx.db.commit()
    ctx.db.refresh(inv)
    return invoice_out(inv, today, detail=True)


# --------------------------------------------------------------------------
# buyer replies: read (AI or rules) -> a person confirms -> it takes effect
# --------------------------------------------------------------------------

INTENT_WORDS = {"promise": "a promise to pay", "dispute": "a dispute", "refusal": "a refusal",
                "question": "a question", "noise": "nothing actionable"}


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ReplyConfirmIn(ReplyIn):
    intent: str = Field(pattern="^(promise|dispute|refusal|question|noise)$")
    channel: str = Field(default="whatsapp", pattern="^(whatsapp|email|phone|in_person|sms|other)$")
    received_on: date | None = None
    promised_date: date | None = None
    amount: str = Field(default="full", pattern="^(full|partial)$")
    suggested_intent: str | None = None
    suggested_by: str | None = Field(default=None, pattern="^(ai|rules)$")

    @model_validator(mode="after")
    def _check(self):
        if self.intent == "promise" and self.promised_date is None:
            raise ValueError("a promise needs the date they promised to pay by")
        return self


@router.post("/{invoice_id}/replies/read")
def read_reply(invoice_id: str, body: ReplyIn, ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Suggest what a pasted reply means. Changes nothing but the audit trail."""
    inv = _load(ctx, invoice_id)
    today = today_for()
    suggestion = replies.read(body.text, today, bridge.outstanding(inv, today))
    reason = f"read the buyer's reply as {INTENT_WORDS[suggestion['intent']]}"
    if suggestion.get("date"):
        reason += f", paying by {suggestion['date']}"
    if suggestion.get("downgraded"):
        reason += f"; not taken at face value: {'; '.join(suggestion['downgraded'])}"
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor="agent",
                 source="llm" if suggestion["reader"] == "ai" else "rule",
                 action="reply_read", reason=reason, invoice_number=inv.invoice_number,
                 buyer_name=inv.buyer.name, detail={"reply": body.text, **suggestion})
    ctx.db.commit()
    return suggestion


@router.post("/{invoice_id}/replies", status_code=status.HTTP_201_CREATED)
def confirm_reply(invoice_id: str, body: ReplyConfirmIn,
                  ctx: TenantContext = Depends(tenant_context)) -> dict:
    """A person says what the reply meant; Recova acts on THAT, not the guess."""
    inv = _load(ctx, invoice_id)
    today = today_for()
    if body.intent == "promise" and body.promised_date < today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "A promise has to be for today or a future date")
    ctx.db.add(BuyerReply(
        tenant_id=ctx.tenant.id, invoice_id=inv.id, received_on=body.received_on or today,
        channel=body.channel, text=body.text, suggested_intent=body.suggested_intent,
        suggested_by=body.suggested_by, intent=body.intent,
        promised_date=body.promised_date if body.intent == "promise" else None,
        recorded_by=ctx.user.email))
    effect = "no change to the ladder"
    if body.intent == "promise":
        ctx.db.add(Promise(tenant_id=ctx.tenant.id, invoice_id=inv.id, recorded_on=today,
                           promised_date=body.promised_date, amount=body.amount,
                           note=f"from the buyer's reply: {body.text[:200]}"))
        effect = f"promise ({body.amount}) recorded for {body.promised_date}; reminders pause"
    elif body.intent == "dispute":
        inv.disputed, inv.dispute_note = True, body.text[:300]
        effect = "invoice marked disputed; automated chasing stops and it goes to a person"
    guess = ""
    if body.suggested_intent:
        by = "the AI" if body.suggested_by == "ai" else "the rules"
        guess = (f"{by} read it as {body.suggested_intent}; "
                 + ("confirmed" if body.suggested_intent == body.intent
                    else f"corrected to {body.intent}") + "; ")
    _note(ctx, inv, "reply_recorded",
          f"buyer replied by {body.channel.replace('_', ' ')}: {guess}{effect}",
          reply=body.text, intent=body.intent, suggested_intent=body.suggested_intent,
          suggested_by=body.suggested_by)
    ctx.db.commit()
    ctx.db.refresh(inv)
    return invoice_out(inv, today, detail=True)


# --------------------------------------------------------------------------
# Razorpay payment links
# --------------------------------------------------------------------------

@router.post("/{invoice_id}/payment-link", status_code=status.HTTP_201_CREATED)
def make_payment_link(invoice_id: str, ctx: TenantContext = Depends(tenant_context)) -> dict:
    inv = _load(ctx, invoice_id)
    today = today_for()
    try:
        payments.link_for(ctx.db, inv, ctx.tenant, today, ctx.user.email)
    except payments.PaymentsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    ctx.db.commit()
    ctx.db.refresh(inv)
    return invoice_out(inv, today, detail=True)


@router.post("/{invoice_id}/payment-link/check")
def check_payment_link(invoice_id: str, ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Ask Razorpay whether the open link was paid (works without a webhook)."""
    inv = _load(ctx, invoice_id)
    link = payments.open_link(ctx.db, inv)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open payment link on this invoice")
    try:
        state = payments.refresh(ctx.db, link)
    except payments.PaymentsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Couldn't reach Razorpay: {exc}")
    ctx.db.commit()
    ctx.db.refresh(inv)
    return invoice_out(inv, today_for(), detail=True) | {"link_status": state}


@router.post("/{invoice_id}/dispute")
def set_dispute(invoice_id: str, body: DisputeIn,
                ctx: TenantContext = Depends(tenant_context)) -> dict:
    inv = _load(ctx, invoice_id)
    inv.disputed, inv.dispute_note = body.disputed, body.note
    _note(ctx, inv, "dispute_opened" if body.disputed else "dispute_resolved",
          ("buyer disputes this invoice -- automated chasing stops and it goes to a person"
           if body.disputed else "dispute resolved; the invoice re-enters the normal ladder")
          + (f": {body.note}" if body.note else ""))
    ctx.db.commit()
    return invoice_out(inv, today_for(), detail=True)


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------

CSV_COLUMNS = ("invoice_number", "buyer_name", "amount_rupees", "issue_date", "acceptance_date",
               "written_agreement", "agreed_days", "description", "po_number")


@router.get("/import/template")
def import_template() -> dict:
    return {"columns": list(CSV_COLUMNS),
            "example": "INV-1001,Vistara Traders,125000.50,2026-07-01,2026-07-03,yes,30,"
                       "Steel brackets,PO-778"}


@router.post("/import")
async def import_csv(file: UploadFile = File(...),
                     ctx: TenantContext = Depends(tenant_context)) -> dict:
    """Create invoices (and any new buyers, by name) from a CSV. Row by row:
    a bad row is reported with its reason and skipped, never half-imported."""
    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("invoice_number", "buyer_name", "amount_rupees", "issue_date",
                           "acceptance_date") if c not in (reader.fieldnames or [])]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"CSV is missing column(s): {', '.join(missing)}")

    buyers = {b.name.lower(): b for b in ctx.db.scalars(ctx.scoped(Buyer)).all()}
    existing = {i.invoice_number for i in ctx.db.scalars(ctx.scoped(Invoice)).all()}
    created, new_buyers, errors = 0, 0, []
    for line, row in enumerate(reader, start=2):
        try:
            number = (row.get("invoice_number") or "").strip()
            if not number:
                raise ValueError("invoice_number is empty")
            if number in existing:
                raise ValueError(f"invoice {number} already exists")
            amount = round(float((row.get("amount_rupees") or "").replace(",", "")) * 100)
            if amount <= 0:
                raise ValueError("amount must be positive")
            written = (row.get("written_agreement") or "").strip().lower() in ("yes", "y", "true", "1")
            agreed = int(row["agreed_days"]) if (row.get("agreed_days") or "").strip() else None
            if written and agreed is None:
                raise ValueError("written_agreement is yes but agreed_days is empty")
            issue = date.fromisoformat(row["issue_date"].strip())
            accept = date.fromisoformat(row["acceptance_date"].strip())
            if accept < issue:
                raise ValueError("acceptance_date is before issue_date")
            name = (row.get("buyer_name") or "").strip()
            if not name:
                raise ValueError("buyer_name is empty")
            buyer = buyers.get(name.lower())
            if buyer is None:
                buyer = Buyer(tenant_id=ctx.tenant.id, name=name,
                              code=f"BUY-{len(buyers) + 1:03d}")
                ctx.db.add(buyer)
                ctx.db.flush()
                buyers[name.lower()] = buyer
                new_buyers += 1
            ctx.db.add(Invoice(tenant_id=ctx.tenant.id, buyer_id=buyer.id, invoice_number=number,
                               amount_paise=amount, issue_date=issue, acceptance_date=accept,
                               written_agreement=written, agreed_days=agreed,
                               description=(row.get("description") or None),
                               po_number=(row.get("po_number") or None)))
            existing.add(number)
            created += 1
        except (ValueError, KeyError) as exc:
            errors.append({"line": line, "error": str(exc)})
    audit.record(ctx.db, tenant_id=ctx.tenant.id, actor=ctx.user.email, action="invoices_imported",
                 reason=f"imported {created} invoice(s) and {new_buyers} new buyer(s) from "
                        f"{file.filename}; {len(errors)} row(s) skipped",
                 detail={"errors": errors[:50]})
    ctx.db.commit()
    return {"created": created, "new_buyers": new_buyers, "skipped": errors}
