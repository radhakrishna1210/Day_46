"""Tests for engine/validate.py.

One test per docs/edge_cases.md TC id, the same convention test_promises.py
uses for its own sanity-bounds section. The point of this module: nothing
malformed reaches engine/law.py or engine/brain.py unnoticed -- so alongside
the six TC tests, this file also proves the actual choke point
(watchdog.overdue_invoices) really excludes what validate.py flags, and that
the exclusion is never silent (engine/audit.py records it).
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import audit, validate, watchdog

TODAY = date(2026, 8, 25)


def invoice(**overrides) -> dict:
    return {
        "invoice_id": "INV-2026-0204",
        "buyer_id": "BUY-01",
        "amount_paise": 50_000_000,
        "issue_date": "2026-06-01",
        "acceptance_date": "2026-06-01",
        "written_agreement": False,
        "agreed_days": None,
        "status": "open",
        **overrides,
    }


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


# --- a well-formed invoice is left alone ------------------------------------

def test_a_well_formed_invoice_is_valid() -> None:
    assert validate.invalid_reason(invoice(), TODAY) is None


# --- one test per TC id -----------------------------------------------------

def test_tc045_missing_acceptance_date_is_rejected() -> None:
    reason = validate.invalid_reason(invoice(acceptance_date=None), TODAY)
    assert reason is not None
    assert "acceptance date" in reason


def test_tc049_non_numeric_agreed_days_is_rejected() -> None:
    reason = validate.invalid_reason(
        invoice(written_agreement=True, agreed_days="whenever possible"), TODAY,
    )
    assert reason is not None
    assert "agreed_days" in reason


def test_tc049_a_numeric_agreed_days_is_not_flagged_by_this_check() -> None:
    """Even a term over the statutory ceiling is a LAW question, not a validity one."""
    assert validate.invalid_reason(
        invoice(written_agreement=True, agreed_days=90), TODAY,
    ) is None


def test_tc050_future_issue_date_is_rejected() -> None:
    reason = validate.invalid_reason(
        invoice(issue_date="2026-09-01", acceptance_date="2026-09-01"), TODAY,
    )
    assert reason is not None
    assert "future" in reason


def test_tc050_stops_being_invalid_once_today_catches_up_to_it() -> None:
    """Unlike the other five, TC-050's own defect is clock-relative: it is not

    a permanently broken record, just one whose issue date has not arrived
    yet. sim/run_sim.py's watchdog check re-evaluates this fresh every
    simulated day for exactly this reason -- see run_agent()/run_baseline()'s
    comment on why validation for the final report is checked at last_day,
    not day0.
    """
    fixture = invoice(issue_date="2026-09-01", acceptance_date="2026-09-01")
    assert validate.invalid_reason(fixture, date(2026, 8, 25)) is not None
    assert validate.invalid_reason(fixture, date(2026, 9, 1)) is None
    assert validate.invalid_reason(fixture, date(2026, 10, 1)) is None


def test_tc051_acceptance_before_issue_date_is_rejected() -> None:
    reason = validate.invalid_reason(
        invoice(issue_date="2026-08-10", acceptance_date="2026-08-05"), TODAY,
    )
    assert reason is not None
    assert "chronology" in reason


def test_tc053_zero_amount_is_rejected() -> None:
    reason = validate.invalid_reason(invoice(amount_paise=0), TODAY)
    assert reason is not None
    assert "amount" in reason


def test_tc054_negative_amount_is_rejected() -> None:
    reason = validate.invalid_reason(invoice(amount_paise=-500_000), TODAY)
    assert reason is not None
    assert "amount" in reason


# --- TC-052: a duplicate invoice_id cannot be safely counted as one --------
# Unlike the six checks above, this needs the WHOLE batch -- a single invoice
# can never know on its own that it is a duplicate. See duplicate_reasons()'s
# own docstring for why this matters: with no dedup anywhere else in the
# pipeline, a duplicate would otherwise be silently double-counted in every
# headline money figure (non-negotiable #5).

def test_tc052_a_duplicate_invoice_id_is_flagged_for_both_records() -> None:
    first = invoice(invoice_id="INV-DUP")
    second = invoice(invoice_id="INV-DUP", amount_paise=1_000_000)
    reasons = validate.duplicate_reasons([first, second])
    assert set(reasons) == {"INV-DUP"}
    assert "appears 2 times" in reasons["INV-DUP"]


def test_tc052_a_lone_invoice_id_is_never_flagged_as_a_duplicate() -> None:
    assert validate.duplicate_reasons([invoice(), invoice(invoice_id="INV-OTHER")]) == {}


def test_tc052_invalid_reason_alone_cannot_see_a_duplicate() -> None:
    """The documented asymmetry: one invoice cannot know it is one of a pair.

    reasons_for(), not invalid_reason(), is where TC-052 is actually caught.
    """
    first = invoice(invoice_id="INV-DUP")
    assert validate.invalid_reason(first, TODAY) is None


def test_tc052_reasons_for_catches_what_invalid_reason_alone_cannot() -> None:
    first = invoice(invoice_id="INV-DUP")
    second = invoice(invoice_id="INV-DUP")
    reasons = validate.reasons_for([first, second], TODAY)
    assert set(reasons) == {"INV-DUP"}


# --- reasons_for / audit_invalid --------------------------------------------

def test_reasons_for_reports_only_the_invalid_ones() -> None:
    good = invoice(invoice_id="INV-GOOD")
    bad = invoice(invoice_id="INV-BAD", amount_paise=0)
    reasons = validate.reasons_for([good, bad], TODAY)
    assert list(reasons) == ["INV-BAD"]


def test_audit_invalid_logs_exactly_once_per_invalid_invoice() -> None:
    bad = invoice(invoice_id="INV-BAD", amount_paise=0)
    validate.audit_invalid([invoice(), bad], TODAY, log=True)
    entries = audit.entries_for("INV-BAD")
    assert len(entries) == 1
    assert entries[0]["action"] == "invoice_validation_failed"
    assert entries[0]["source"] == "rule"
    assert "amount" in entries[0]["reason"]


def test_audit_invalid_can_run_silently() -> None:
    bad = invoice(invoice_id="INV-BAD", amount_paise=0)
    reasons = validate.audit_invalid([bad], TODAY, log=False)
    assert reasons == {"INV-BAD": reasons["INV-BAD"]}
    assert audit.entries() == []


# --- the choke point: nothing malformed reaches the queue -------------------

@pytest.mark.parametrize("overrides", [
    {"acceptance_date": None},                                            # TC-045
    {"written_agreement": True, "agreed_days": "whenever possible"},      # TC-049
    {"issue_date": "2026-09-01", "acceptance_date": "2026-09-01"},        # TC-050
    {"issue_date": "2026-08-10", "acceptance_date": "2026-08-05"},        # TC-051
    {"amount_paise": 0},                                                  # TC-053
    {"amount_paise": -500_000},                                          # TC-054
])
def test_an_invalid_invoice_never_enters_the_overdue_queue(overrides) -> None:
    """The literal requirement: nothing malformed reaches law.py or brain.py.

    Every one of these would crash or silently misreport if it reached
    engine/law.py -- see the investigation behind this change. Here it must
    not even crash watchdog.overdue_invoices() itself.
    """
    bad = invoice(**overrides)
    assert watchdog.overdue_invoices([bad], TODAY) == []
    assert watchdog.is_overdue(bad, TODAY) is False


def test_a_valid_overdue_invoice_still_enters_the_queue() -> None:
    """Validation must not become a second, accidental way to hide real work."""
    good = invoice()
    queue = watchdog.overdue_invoices([good], TODAY)
    assert [inv["invoice_id"] for inv in queue] == [good["invoice_id"]]


def test_an_invalid_invoice_among_valid_ones_does_not_break_the_rest() -> None:
    good = invoice(invoice_id="INV-GOOD")
    bad = invoice(invoice_id="INV-BAD", acceptance_date=None)
    queue = watchdog.overdue_invoices([good, bad], TODAY)
    assert [inv["invoice_id"] for inv in queue] == ["INV-GOOD"]


def test_tc052_a_duplicate_invoice_id_never_enters_the_overdue_queue() -> None:
    """is_overdue() alone cannot catch this -- see test_tc052_invalid_reason_
    alone_cannot_see_a_duplicate -- so overdue_invoices() has to check
    duplicate_reasons() itself, not just delegate to is_overdue() per invoice.
    """
    first = invoice(invoice_id="INV-DUP")
    second = invoice(invoice_id="INV-DUP")
    assert watchdog.overdue_invoices([first, second], TODAY) == []


def test_tc052_a_duplicate_does_not_hide_a_genuinely_separate_invoice() -> None:
    good = invoice(invoice_id="INV-GOOD")
    dup_a = invoice(invoice_id="INV-DUP")
    dup_b = invoice(invoice_id="INV-DUP")
    queue = watchdog.overdue_invoices([good, dup_a, dup_b], TODAY)
    assert [inv["invoice_id"] for inv in queue] == ["INV-GOOD"]
