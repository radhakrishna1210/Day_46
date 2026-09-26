"""Razorpay Payment Links: a "pay now" link per invoice, and recording the
money when -- and only when -- Razorpay says it was paid.

Configured in saas/api/.env: RAZORPAY_KEY_ID + RAZORPAY_KEY_SECRET (test keys
start rzp_test_ -- build and try everything in test mode first), and
RAZORPAY_WEBHOOK_SECRET for the signed webhook. With no keys the feature is
simply off: no links, nothing breaks.

Two ways a payment reaches Recova, both idempotent:
  * the webhook (payment_link.paid), signature-checked -- needs a public URL,
    so it works once deployed (or through a tunnel);
  * asking Razorpay (refresh) -- the "Check payment" button and the daily run,
    so a laptop with no public URL still sees payments.
Buyers are never notified by Razorpay (notify off): Recova's own reminder
carries the link, under the same stop rules as every other message.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, bridge, settings
from app.clock import today_for
from app.models import Invoice, Payment, PaymentLink, Tenant

log = logging.getLogger("recova.payments")
API = "https://api.razorpay.com/v1"


class PaymentsError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(settings.env("RAZORPAY_KEY_ID") and settings.env("RAZORPAY_KEY_SECRET"))


def mode() -> str | None:
    key = settings.env("RAZORPAY_KEY_ID")
    return None if not key else "test" if key.startswith("rzp_test_") else "live"


def _auth() -> tuple[str, str]:
    return settings.env("RAZORPAY_KEY_ID"), settings.env("RAZORPAY_KEY_SECRET")


# Seams for tests: replaced with fakes so no test ever calls Razorpay.
def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    res = httpx.post(f"{API}{path}", json=body, auth=_auth(), timeout=20)
    if res.status_code >= 400:
        raise PaymentsError(_error(res))
    return res.json()


def _get(path: str) -> dict[str, Any]:
    res = httpx.get(f"{API}{path}", auth=_auth(), timeout=20)
    if res.status_code >= 400:
        raise PaymentsError(_error(res))
    return res.json()


def _error(res: httpx.Response) -> str:
    try:
        return res.json()["error"]["description"]
    except Exception:  # noqa: BLE001
        return f"Razorpay said {res.status_code}"


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------

def open_link(db: Session, inv: Invoice) -> PaymentLink | None:
    return db.scalar(select(PaymentLink).where(PaymentLink.invoice_id == inv.id,
                                               PaymentLink.status == "created")
                     .order_by(PaymentLink.created_at.desc()))


def link_for(db: Session, inv: Invoice, tenant: Tenant, today: date, actor: str) -> PaymentLink:
    """The live link for what is owed today -- reused when the amount still
    matches, otherwise the old one is cancelled and a fresh one made."""
    if not enabled():
        raise PaymentsError("Payments aren't set up on this server")
    owed = bridge.outstanding(inv, today)
    if owed <= 0:
        raise PaymentsError("Nothing is outstanding on this invoice")
    if inv.disputed:
        raise PaymentsError("This invoice is disputed -- sort that out before asking for payment")
    current = open_link(db, inv)
    if current and current.amount_paise == owed:
        return current
    if current:
        try:
            _post(f"/payment_links/{current.provider_id}/cancel", {})
        except PaymentsError as exc:
            log.warning("could not cancel %s: %s", current.provider_id, exc)
        current.status = "cancelled"
    count = len(db.scalars(select(PaymentLink.id).where(PaymentLink.invoice_id == inv.id)).all())
    buyer = inv.buyer
    customer = {k: v for k, v in {"name": buyer.name, "email": buyer.contact_email,
                                  "contact": buyer.contact_phone}.items() if v}
    body = {
        "amount": owed, "currency": "INR", "accept_partial": False,
        "description": f"Invoice {inv.invoice_number} from {tenant.legal_name}"[:2048],
        "reference_id": f"{inv.id[:24]}-{count + 1}",
        "customer": customer,
        "notify": {"sms": False, "email": False},   # Recova's reminder carries the link
        "reminder_enable": False,
        "notes": {"tenant_id": tenant.id, "invoice_id": inv.id,
                  "invoice_number": inv.invoice_number},
    }
    got = _post("/payment_links", body)
    link = PaymentLink(tenant_id=tenant.id, invoice_id=inv.id, provider_id=got["id"],
                       short_url=got["short_url"], amount_paise=owed, created_by=actor)
    db.add(link)
    audit.record(db, tenant_id=tenant.id, actor=actor, action="payment_link_created",
                 reason=f"Razorpay payment link for {owed} paise ({mode()} mode)",
                 invoice_number=inv.invoice_number, buyer_name=buyer.name,
                 detail={"link": got["id"], "url": got["short_url"], "amount_paise": owed})
    return link


# --------------------------------------------------------------------------
# recording the money
# --------------------------------------------------------------------------

def _settle(db: Session, link: PaymentLink, amount_paise: int, payment_id: str | None,
            via: str) -> bool:
    """Record a paid link exactly once. Returns False if it was already done."""
    if link.status == "paid":
        return False
    inv = db.get(Invoice, link.invoice_id)
    today = today_for()
    owed = bridge.outstanding(inv, today)
    amount = min(amount_paise, owed) if owed > 0 else 0
    link.status, link.paid_at, link.provider_payment_id = "paid", datetime.now(timezone.utc), payment_id
    if amount > 0:
        db.add(Payment(tenant_id=link.tenant_id, invoice_id=inv.id, paid_on=today,
                       amount_paise=amount, note=f"Razorpay {payment_id or link.provider_id}"))
        for promise in inv.promises:
            if promise.status == "open" and today <= promise.promised_date:
                promise.status = "kept"
    audit.record(db, tenant_id=link.tenant_id, actor="razorpay", source="rule",
                 action="payment_received_online",
                 reason=(f"Razorpay confirmed {amount_paise} paise paid on link {link.provider_id} "
                         f"({via}); recorded {amount} paise against the invoice"
                         + ("" if amount == amount_paise else " -- capped at what was still owed")),
                 invoice_number=inv.invoice_number, buyer_name=inv.buyer.name,
                 detail={"link": link.provider_id, "payment": payment_id, "via": via,
                         "paid_paise": amount_paise, "recorded_paise": amount})
    return True


def refresh(db: Session, link: PaymentLink) -> str:
    """Ask Razorpay what happened to one link; record it if paid."""
    got = _get(f"/payment_links/{link.provider_id}")
    state = got.get("status")
    if state == "paid":
        payments = got.get("payments") or []
        pid = payments[-1].get("payment_id") if payments else None
        _settle(db, link, int(got.get("amount_paid") or link.amount_paise), pid, "checked with Razorpay")
    elif state in ("cancelled", "expired") and link.status == "created":
        link.status = state
    return link.status


def refresh_open(db: Session, tenant_id: str | None = None) -> int:
    """Check every open link (the daily run). Returns how many turned out paid."""
    if not enabled():
        return 0
    q = select(PaymentLink).where(PaymentLink.status == "created")
    if tenant_id:
        q = q.where(PaymentLink.tenant_id == tenant_id)
    paid = 0
    for link in db.scalars(q).all():
        try:
            if refresh(db, link) == "paid":
                paid += 1
        except PaymentsError as exc:
            log.warning("refresh %s failed: %s", link.provider_id, exc)
    return paid


def verify_signature(raw: bytes, signature: str | None) -> bool:
    secret = settings.env("RAZORPAY_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook(db: Session, event: dict[str, Any]) -> str:
    if event.get("event") != "payment_link.paid":
        return "ignored"
    payload = event.get("payload", {})
    entity = payload.get("payment_link", {}).get("entity", {})
    link = db.scalar(select(PaymentLink).where(PaymentLink.provider_id == entity.get("id")))
    if link is None:
        return "unknown link"
    payment = payload.get("payment", {}).get("entity", {})
    done = _settle(db, link, int(entity.get("amount_paid") or payment.get("amount") or 0),
                   payment.get("id"), "webhook")
    return "recorded" if done else "already recorded"
