"""Tests for the Samadhaan complaint draft generator.

The draft is the most consequential document this system produces: a human may
act on it. So the tests care about two things above all --

  * no placeholder survives into the output. A draft with an unfilled {field}
    or a stray "None" is worse than no draft, because it looks finished.
  * a draft is never marked READY TO FILE while anything is missing. The
    placeholder Udyam number in config/supplier.yaml must block it loudly.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from engine import law, samadhaan
from engine.config import supplier

TODAY = date(2026, 8, 24)


def invoice(
    *,
    acceptance: str | None = "2026-01-01",
    written: bool = True,
    agreed_days: int | None = 90,
    amount: int = 50_000_000,
    status: str = "open",
    payments: list[dict] | None = None,
) -> dict:
    payments = payments or []
    record = {
        "invoice_id": "INV-2026-0204",
        "buyer_id": "BUY-01",
        "description": "400 kg HDPE granules, batch B-2214",
        "po_number": "PO/25-26/04821",
        "issue_date": "2025-12-28",
        "acceptance_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": "2026-04-01",
        "amount_paise": amount,
        "currency": "INR",
        "status": status,
        "partial_payments": payments,
        "amount_paid_paise": sum(p["amount_paise"] for p in payments),
        "paid_date": None,
        "disputed": status == "disputed",
        "dispute_note": "Buyer raised a quality complaint." if status == "disputed" else None,
    }
    return record


BUYER = {
    "buyer_id": "BUY-01", "name": "ABC Traders Private Limited",
    "profile": "corporate", "city": "Pune", "state": "Maharashtra",
    "gstin": "27AABCA1234B1Z5", "contact_name": "R. Kumar",
    "contact_email": "r.kumar@abc-traders.example.invalid",
    "contact_phone": "+91-90000-00001",
}


def build(record: dict | None = None, buyer: dict | None = None) -> dict:
    record = record if record is not None else invoice()
    position = law.legal_position(record, TODAY)
    return samadhaan.build_draft(record, buyer or BUYER, position, TODAY)


# --- readiness ------------------------------------------------------------

def test_the_placeholder_udyam_number_blocks_filing() -> None:
    """config/supplier.yaml ships a fake registration. It must fail loudly."""
    result = build()
    assert result["ready"] is False
    reasons = " ".join(result["blockers"]).lower()
    assert "udyam" in reasons


def test_a_real_looking_udyam_number_clears_that_check(monkeypatch: pytest.MonkeyPatch) -> None:
    real = {
        **supplier(),
        "supplier": {**supplier()["supplier"], "udyam_registration": "UDYAM-KR-03-0123456"},
    }
    monkeypatch.setattr(samadhaan, "supplier", lambda: real)
    result = build()
    assert result["ready"] is True, result["blockers"]
    assert result["blockers"] == []


def test_a_live_dispute_blocks_filing() -> None:
    result = build(invoice(status="disputed"))
    assert result["ready"] is False
    assert any("dispute" in reason.lower() for reason in result["blockers"])


def test_a_missing_acceptance_date_blocks_filing() -> None:
    record = invoice()
    record["acceptance_date"] = None
    result = samadhaan.build_draft(record, BUYER, None, TODAY)
    assert result["ready"] is False
    assert any("acceptance" in reason.lower() for reason in result["blockers"])


def test_an_invoice_not_overdue_enough_blocks_filing() -> None:
    """We do not reference a buyer who is three days late."""
    recent = invoice(acceptance="2026-07-20", written=False, agreed_days=None)
    result = build(recent)
    assert result["ready"] is False
    assert any("overdue" in reason.lower() for reason in result["blockers"])


def test_a_fully_settled_invoice_blocks_filing() -> None:
    settled = invoice(payments=[{"date": "2026-05-01", "amount_paise": 50_000_000}],
                      status="paid")
    result = build(settled)
    assert result["ready"] is False
    assert any("outstanding" in reason.lower() for reason in result["blockers"])


def test_missing_purchase_order_is_a_warning_not_a_blocker() -> None:
    record = invoice()
    record["po_number"] = None
    result = build(record)
    assert any("purchase order" in warning.lower() for warning in result["warnings"])
    assert not any("purchase order" in reason.lower() for reason in result["blockers"])


def test_missing_buyer_gstin_is_a_warning_not_a_blocker() -> None:
    result = build(buyer={**BUYER, "gstin": None})
    assert any("gstin" in warning.lower() for warning in result["warnings"])
    assert not any("gstin" in reason.lower() for reason in result["blockers"])


def test_inconsistent_payment_records_block_filing() -> None:
    record = invoice(payments=[{"date": "2026-05-01", "amount_paise": 10_000_000}])
    record["amount_paid_paise"] = 99_999               # does not reconcile
    result = build(record)
    assert result["ready"] is False
    assert any("reconcile" in reason.lower() for reason in result["blockers"])


# --- the rendered document ------------------------------------------------

def test_no_unfilled_placeholder_survives() -> None:
    """DEFINITION OF DONE: every field renders."""
    text = build()["markdown"]
    leftovers = re.findall(r"\{[a-z_]+\}", text)
    assert not leftovers, f"unfilled placeholders: {leftovers}"


def test_no_stray_none_reaches_the_page() -> None:
    text = build()["markdown"]
    assert not re.search(r"(?<![A-Za-z])None(?![A-Za-z])", text)


def test_the_draft_is_marked_as_a_draft() -> None:
    text = build()["markdown"]
    assert "DRAFT" in text
    assert "NOT" in text.upper()


def test_every_section_is_present() -> None:
    text = build()["markdown"]
    for heading in (
        "Applicant", "Respondent", "Invoice", "Statutory position",
        "Interest computation", "Total claimed", "Relief sought",
        "Supporting documents", "Readiness", "Declaration",
    ):
        assert heading in text, f"missing section: {heading}"


def test_the_parties_are_named() -> None:
    text = build()["markdown"]
    assert supplier()["supplier"]["legal_name"] in text
    assert BUYER["name"] in text
    assert BUYER["state"] in text


def test_the_money_matches_the_law_engine_exactly() -> None:
    from engine.money import format_inr
    record = invoice()
    position = law.legal_position(record, TODAY)
    text = samadhaan.build_draft(record, BUYER, position, TODAY)["markdown"]
    assert format_inr(position["principal_paise"]) in text
    assert format_inr(position["interest_paise"], decimals=True) in text
    assert format_inr(position["total_payable_paise"], decimals=True) in text


def test_the_interest_table_shows_its_working() -> None:
    text = build()["markdown"]
    for expected in ("16.50", "5.50", "monthly rests", "2026-02-16"):
        assert expected in text, f"interest working missing: {expected}"


def test_the_void_contract_term_is_called_out() -> None:
    """The 90-day term is why the statutory date is 45 days, and it must show."""
    text = build()["markdown"]
    assert "90" in text and "45" in text


def test_partial_payments_are_itemised() -> None:
    record = invoice(payments=[{"date": "2026-05-01", "amount_paise": 10_000_000}],
                     status="partially_paid")
    text = build(record)["markdown"]
    assert "2026-05-01" in text


def test_a_blocked_draft_states_why_on_the_page() -> None:
    text = build()["markdown"]
    assert "BLOCKED" in text.upper()
    assert "udyam" in text.lower()


def test_the_footer_records_the_rate_and_its_retrieval_date() -> None:
    """An auditor must know which bank rate this draft was built on."""
    text = build()["markdown"]
    assert "2026-08-23" in text
    assert "5.50" in text


def test_the_disclaimer_is_present() -> None:
    text = build()["markdown"]
    assert "not legal advice" in text.lower()


# --- writing to disk ------------------------------------------------------

def test_the_draft_writes_to_the_audit_drafts_folder(tmp_path) -> None:
    record = invoice()
    position = law.legal_position(record, TODAY)
    written = samadhaan.write_draft(record, BUYER, position, TODAY, out_dir=tmp_path)
    assert written.exists()
    assert written.name == "samadhaan-INV-2026-0204.md"
    assert written.read_text(encoding="utf-8") == build()["markdown"]


def test_the_default_output_location_is_under_audit() -> None:
    assert samadhaan.DEFAULT_DRAFT_DIR.parts[-2:] == ("audit", "drafts")


# --- both consumers read from the single source --------------------------

def test_the_draft_and_a_message_quote_identical_section_16_wording() -> None:
    """The refactor's whole point: one wording, two consumers.

    The clause is rendered once from config and must appear verbatim in both
    the buyer-facing fact and the Samadhaan draft.
    """
    record = invoice()
    position = law.legal_position(record, TODAY)
    clause = law.render_clauses({
        "effective_rate_pct": f"{law.effective_annual_rate() * 100:.2f}",
        "bank_rate_pct": f"{float(law.legal()['rbi_bank_rate']) * 100:.2f}",
    })["section_16_rate"]

    message_fact = position["facts_by_key"]["section_16"]
    draft = samadhaan.build_draft(record, BUYER, position, TODAY)["markdown"]

    assert clause in message_fact, "the message does not use the canonical clause"
    assert clause in draft, "the draft does not use the canonical clause"


def test_the_predeposit_is_worded_per_audience_but_shares_one_figure() -> None:
    """The draft says "the Respondent"; a message speaks to the buyer directly.

    Different phrasing is the point of the split. The percentage is not allowed
    to differ, because that is a legal figure and both come from one config
    value through the same renderer.
    """
    record = invoice()
    position = law.legal_position(record, TODAY)
    rendered = law.render_clauses({"predeposit_pct": "75"})
    formal = rendered["section_19_predeposit_formal"]
    plain = rendered["section_19_predeposit_plain"]

    draft = samadhaan.build_draft(record, BUYER, position, TODAY)["markdown"]
    message = position["facts_by_key"]["samadhaan"]

    assert formal in draft, "the draft does not use the formal clause"
    assert plain in message, "the message does not use the plain clause"
    assert formal not in message and plain not in draft, "the wordings crossed over"

    # the wording differs; the figure does not
    assert "Respondent" in formal and "Respondent" not in plain
    assert "75%" in formal and "75%" in plain


def test_clauses_resolve_from_the_law_engine_value_set() -> None:
    """Every clause must be buildable where messages are rendered."""
    position = law.legal_position(invoice(), TODAY)
    assert position["facts_by_key"], "no facts rendered at all"
    for fact in position["facts_by_key"].values():
        assert "{" not in fact, f"unresolved placeholder in: {fact}"
