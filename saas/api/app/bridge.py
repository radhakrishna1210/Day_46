"""Database rows -> engine records -> engine answers.

The engine (repo-root engine/) is used unchanged, as a library: it reads plain
dicts and a clock, and it never learns where they came from. This module is
the one place that builds those dicts from a tenant's rows, and the one place
that asks the engine questions. Rules decide; nothing here overrides one.

Everything runs with logging OFF on the engine side (its own audit trail is a
JSONL file for the simulator) -- the SaaS writes its own per-tenant audit rows
in the routers, for actions a person actually takes.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine import brain, law, rungs, watchdog  # noqa: E402
from engine import score as score_engine  # noqa: E402
from engine.config import supplier, supplier_context  # noqa: E402

from app.models import Buyer, Invoice, Tenant  # noqa: E402


# --------------------------------------------------------------------------
# rows -> engine records
# --------------------------------------------------------------------------

def buyer_record(buyer: Buyer) -> dict[str, Any]:
    return {
        "buyer_id": buyer.id, "name": buyer.name, "profile": buyer.profile,
        "sector": buyer.sector, "language_pref": buyer.language_pref,
        "contact_name": buyer.contact_name, "contact_email": buyer.contact_email,
        "contact_phone": buyer.contact_phone, "city": buyer.city, "state": buyer.state,
        "gstin": buyer.gstin, "preferred_channel": buyer.preferred_channel,
        "opted_out": buyer.opted_out,
    }


def invoice_record(inv: Invoice) -> dict[str, Any]:
    payments = [{"date": p.paid_on.isoformat(), "amount_paise": p.amount_paise}
                for p in inv.payments]
    paid = sum(p.amount_paise for p in inv.payments)
    status = "paid" if paid >= inv.amount_paise else ("partially_paid" if paid else "open")
    agreed_due = (inv.acceptance_date + timedelta(days=inv.agreed_days)).isoformat() \
        if inv.written_agreement and inv.agreed_days else None
    return {
        "invoice_id": inv.invoice_number, "buyer_id": inv.buyer_id, "cohort": "current",
        "description": inv.description, "po_number": inv.po_number,
        "amount_paise": inv.amount_paise, "currency": "INR",
        "issue_date": inv.issue_date.isoformat(),
        "acceptance_date": inv.acceptance_date.isoformat(),
        "written_agreement": inv.written_agreement, "agreed_days": inv.agreed_days,
        "agreed_due_date": agreed_due, "status": status, "partial_payments": payments,
        "amount_paid_paise": paid,
        "paid_date": (max(p.paid_on for p in inv.payments).isoformat()
                      if status == "paid" and inv.payments else None),
        "disputed": inv.disputed, "dispute_note": inv.dispute_note,
    }


def promise_records(inv: Invoice) -> list[dict[str, Any]]:
    return [{"invoice_id": inv.invoice_number, "promised_date": p.promised_date.isoformat(),
             "amount": p.amount, "status": p.status, "recorded_on": p.recorded_on.isoformat()}
            for p in inv.promises]


def history_records(inv: Invoice) -> list[dict[str, Any]]:
    return [{"date": c.contacted_on.isoformat(), "rung": c.rung, "channel": c.channel,
             "outcome": c.outcome} for c in inv.contacts]


def supplier_profile(tenant: Tenant) -> dict[str, Any]:
    """The tenant in config/supplier.yaml's shape, so writer sign-offs and
    Samadhaan drafts carry THIS business, not the demo placeholder. Fields the
    tenant has not filled fall back to the file's placeholders -- which keep
    Samadhaan drafts BLOCKED until a real Udyam number is supplied."""
    base = supplier()
    profile = dict(base["supplier"])
    for key in ("legal_name", "udyam_registration", "enterprise_class", "gstin", "pan",
                "address_line1", "address_line2", "city", "state", "pincode",
                "contact_name", "contact_email", "contact_phone"):
        value = getattr(tenant, key)
        if value:
            profile[key] = value
    return {**base, "supplier": profile}


@contextmanager
def as_tenant(tenant: Tenant):
    with supplier_context(supplier_profile(tenant)):
        yield


# --------------------------------------------------------------------------
# questions for the engine
# --------------------------------------------------------------------------

def outstanding(inv: Invoice, today: date) -> int:
    return law.outstanding_paise(invoice_record(inv), today)


def legal_summary(inv: Invoice, today: date) -> dict[str, Any]:
    """The statutory position of one invoice, trimmed to what a screen needs."""
    pos = law.legal_position(invoice_record(inv), today)
    return {
        "statutory_due_date": pos["statutory_due_date"],
        "interest_from": pos["interest_from"],
        "days_overdue": pos["days_overdue"],
        "principal_paise": pos["principal_paise"],
        "interest_paise": pos["interest_paise"],
        "total_payable_paise": pos["total_payable_paise"],
        "interest_per_day_paise": pos["interest_per_day_paise"],
        "cost_of_waiting_paise": pos["cost_of_waiting_paise"],
        "waiting_horizon_days": pos["waiting_horizon_days"],
        "buyer_tax_exposure_paise": pos["tax_exposure_paise"],
        "tax_deduction_crystallised": pos["tax_deduction_crystallised"],
        "available_rung": pos["available_rung"],
        "agreed_term_void": pos["agreed_term_void"],
        "days_gained_by_law": pos["days_gained_by_law"],
        "dispute_hold": pos["dispute_hold"],
        "facts": list(pos.get("facts_by_key", {}).values()),
    }


def score_buyers(buyers: list[Buyer], invoices: list[Invoice], today: date
                 ) -> dict[str, dict[str, Any]]:
    by_buyer: dict[str, list[dict[str, Any]]] = {}
    for inv in invoices:
        by_buyer.setdefault(inv.buyer_id, []).append(invoice_record(inv))
    return {b.id: score_engine.score_buyer(buyer_record(b), by_buyer.get(b.id, []), today)
            for b in buyers}


RUNG_NAMES = {entry["id"]: entry["name"] for entry in rungs.all_rungs()}


def decide(inv: Invoice, buyer: Buyer, buyer_score: dict[str, Any], today: date
           ) -> dict[str, Any]:
    """What the rules say to do about this invoice today. Never sends anything."""
    record = invoice_record(inv)
    position = law.legal_position(record, today)
    action = brain.decide(record, buyer_record(buyer), buyer_score, position,
                          promises=promise_records(inv), history=history_records(inv),
                          log=False)
    return {
        "kind": action.kind,
        "rung": action.rung,
        "rung_name": RUNG_NAMES.get(action.rung, str(action.rung)),
        "reason": action.reason,
        "source": action.source,
        "available_rung": action.available_rung,
        "next_review_date": (action.next_review_date.isoformat()
                             if action.next_review_date else None),
        "samadhaan": action.detail.get("samadhaan_draft"),
        "skeleton": action.skeleton,
    }


def draft_message(inv: Invoice, buyer: Buyer, buyer_score: dict[str, Any],
                  skeleton: dict[str, Any], today: date) -> dict[str, Any]:
    """The message the writer would send at this rung (mock LLM unless the
    server runs with LLM_MODE=live). Guardrailed exactly as in the engine."""
    from engine import writer   # heavy import; only when a draft is asked for

    draft = writer.write_message(skeleton, invoice=invoice_record(inv),
                                 buyer=buyer_record(buyer), score=buyer_score,
                                 promises=promise_records(inv), today=today, log=False)
    return {"subject": draft.get("subject"), "body": draft.get("body"),
            "language": draft.get("language"), "fallback_used": draft.get("fallback_used"),
            "source": draft.get("source")}


def early_warnings(buyers: list[Buyer], invoices: list[Invoice],
                   scores: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    records = [invoice_record(i) for i in invoices]
    promises = [p for i in invoices for p in promise_records(i)]
    return [w for w in watchdog.early_warnings(records, promises, scores, today)
            if w.get("risk_band") != "low"]
