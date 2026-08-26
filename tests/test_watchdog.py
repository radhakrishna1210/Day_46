"""Tests for the watchdog.

Pure date math, but the consequences are not: an invoice wrongly called overdue
means chasing someone who does not owe anything yet, and a missed one means
money left on the table.
"""

from __future__ import annotations

import copy
import re
from datetime import date

import pytest

from engine import watchdog
from engine.config import legal, rules

TODAY = date(2026, 8, 24)


def invoice(
    *,
    invoice_id: str = "INV-2026-0001",
    acceptance: str = "2026-06-01",
    agreed_days: int | None = 45,
    written: bool = True,
    status: str = "open",
    amount: int = 50_000_000,
    paid: int = 0,
    agreed_due: str | None = None,
) -> dict:
    return {
        "invoice_id": invoice_id,
        "buyer_id": "BUY-01",
        "acceptance_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": agreed_due,
        "status": status,
        "amount_paise": amount,
        "amount_paid_paise": paid,
    }


# --- what counts as still owing ------------------------------------------

@pytest.mark.parametrize(("status", "expected"), [
    ("open", True),
    ("partially_paid", True),
    ("disputed", True),
    ("paid", False),
])
def test_unsettled_statuses(status: str, expected: bool) -> None:
    assert watchdog.is_unsettled(invoice(status=status)) is expected


def test_outstanding_subtracts_partial_payments() -> None:
    assert watchdog.outstanding_paise(invoice(amount=50_000_000, paid=20_000_000)) == 30_000_000


def test_outstanding_of_an_untouched_invoice_is_the_whole_amount() -> None:
    assert watchdog.outstanding_paise(invoice(amount=50_000_000)) == 50_000_000


# --- the date arithmetic --------------------------------------------------

def test_days_overdue_counts_from_the_statutory_due_date() -> None:
    """Accepted 2026-06-01 on 45-day terms: due 2026-07-16, so 39 days by 2026-08-24."""
    assert watchdog.days_overdue(invoice(acceptance="2026-06-01", agreed_days=45), TODAY) == 39


def test_days_overdue_is_negative_before_the_due_date() -> None:
    assert watchdog.days_overdue(invoice(acceptance="2026-08-20", agreed_days=45), TODAY) == -41


def test_an_invoice_due_today_is_not_yet_overdue() -> None:
    """Payment is due on the day itself; chasing at 00:01 would be wrong."""
    due_today = invoice(acceptance="2026-07-10", agreed_days=45)
    assert watchdog.days_overdue(due_today, TODAY) == 0
    assert watchdog.is_overdue(due_today, TODAY) is False


def test_the_day_after_the_due_date_is_overdue() -> None:
    assert watchdog.is_overdue(invoice(acceptance="2026-07-09", agreed_days=45), TODAY) is True


def test_a_ninety_day_contract_goes_overdue_at_forty_five_days() -> None:
    """The whole point of the law engine, seen from the queue."""
    record = invoice(acceptance="2026-07-01", agreed_days=90)
    assert watchdog.is_overdue(record, TODAY) is True
    assert watchdog.days_overdue(record, TODAY) == 9


# --- the queue ------------------------------------------------------------

def test_paid_invoices_never_enter_the_queue() -> None:
    settled = invoice(acceptance="2026-01-01", status="paid")
    assert watchdog.overdue_invoices([settled], TODAY) == []


def test_invoices_not_yet_due_stay_out_of_the_queue() -> None:
    """The watchdog has to filter, not blast the whole table."""
    early = invoice(invoice_id="INV-EARLY", acceptance="2026-08-20")
    late = invoice(invoice_id="INV-LATE", acceptance="2026-06-01")
    queue = watchdog.overdue_invoices([early, late], TODAY)
    assert [inv["invoice_id"] for inv in queue] == ["INV-LATE"]


def test_disputed_invoices_still_appear_in_the_queue() -> None:
    """Finding it is the watchdog's job; handing it to a human is the brain's."""
    disputed = invoice(status="disputed", acceptance="2026-06-01")
    assert len(watchdog.overdue_invoices([disputed], TODAY)) == 1


def test_queue_is_ordered_by_money_at_risk() -> None:
    small = invoice(invoice_id="INV-SMALL", amount=1_000_000, acceptance="2026-01-01")
    large = invoice(invoice_id="INV-LARGE", amount=90_000_000, acceptance="2026-06-01")
    part = invoice(invoice_id="INV-PART", amount=95_000_000, paid=94_000_000, acceptance="2026-05-01")
    queue = watchdog.overdue_invoices([small, large, part], TODAY)
    assert [inv["invoice_id"] for inv in queue] == ["INV-LARGE", "INV-SMALL", "INV-PART"]


def test_work_item_records_the_dates_it_reasoned_from() -> None:
    """The audit trail needs the why, not just the what."""
    item = watchdog.work_item(
        invoice(acceptance="2026-06-01", agreed_days=90, agreed_due="2026-08-30"), TODAY
    )
    assert item["statutory_due_date"] == "2026-07-16"
    assert item["days_overdue"] == 39
    assert item["days_gained_by_law"] == 45


# --- promises -------------------------------------------------------------

def promise(promised: str, status: str = "open") -> dict:
    return {"invoice_id": "INV-2026-0001", "promised_date": promised, "status": status}


def test_a_promise_in_the_future_is_not_broken() -> None:
    assert watchdog.is_promise_broken(promise("2026-09-05"), TODAY) is False


def test_a_promise_due_today_is_not_broken_yet() -> None:
    """They still have the whole day to pay."""
    assert watchdog.is_promise_broken(promise("2026-08-24"), TODAY) is False


def test_a_promise_past_its_date_is_broken() -> None:
    assert watchdog.is_promise_broken(promise("2026-08-23"), TODAY) is True


def test_a_kept_promise_is_never_broken() -> None:
    assert watchdog.is_promise_broken(promise("2026-01-01", status="kept"), TODAY) is False


def test_due_promises_returns_only_the_broken_ones() -> None:
    promises = [
        promise("2026-09-05"),
        promise("2026-08-01"),
        promise("2026-07-01", status="kept"),
    ]
    assert [p["promised_date"] for p in watchdog.due_promises(promises, TODAY)] == ["2026-08-01"]


# --- early warning ----------------------------------------------------------
# TODAY (2026-08-24) + 9 days == 2026-09-02; on 45-day terms that lands on an
# acceptance date of 2026-07-19 -- shared by every "due in 9 days" fixture
# below so the arithmetic only has to be checked once.

def _bad_signal_world() -> tuple[list[dict], list[dict], dict]:
    """One buyer (BUY-01) with all three early-warning categories triggered."""
    target = invoice(invoice_id="INV-TARGET", acceptance="2026-07-19", agreed_days=45)
    prior_1 = invoice(invoice_id="INV-PRIOR-1", acceptance="2026-01-01")   # long overdue
    prior_2 = invoice(invoice_id="INV-PRIOR-2", acceptance="2026-02-01")   # long overdue
    promises = (
        [{"buyer_id": "BUY-01", "status": "broken"}] * 3
        + [{"buyer_id": "BUY-01", "status": "kept"}]
    )
    scores = {"BUY-01": {"score": 32, "confidence": "medium"}}
    return [target, prior_1, prior_2], promises, scores


def test_a_bad_signal_buyers_not_yet_due_invoice_is_flagged() -> None:
    invoices, promises, scores = _bad_signal_world()
    warnings = watchdog.early_warnings(invoices, promises, scores, TODAY)
    assert [w["invoice_id"] for w in warnings] == ["INV-TARGET"]
    warning = warnings[0]
    assert warning["days_until_due"] == 9
    assert warning["risk_band"] == "high"
    assert warning["signals_triggered"] == 3
    assert len(warning["reasons"]) >= 2
    assert any("32" in reason for reason in warning["reasons"])
    assert any("broke 3 of last 4 promises" in reason for reason in warning["reasons"])
    assert any("2 prior invoices went overdue" in reason for reason in warning["reasons"])


def test_a_good_signal_buyers_not_yet_due_invoice_is_not_flagged() -> None:
    target = {**invoice(invoice_id="INV-GOOD", acceptance="2026-07-19", agreed_days=45),
              "buyer_id": "BUY-02"}
    scores = {"BUY-02": {"score": 85, "confidence": "high"}}
    warnings = watchdog.early_warnings([target], [], scores, TODAY)
    assert warnings == []


def test_one_bad_signal_alone_is_not_enough_to_flag() -> None:
    """Consistent with score.py: a single data point is never evidence."""
    target = invoice(invoice_id="INV-TARGET", acceptance="2026-07-19", agreed_days=45)
    scores = {"BUY-01": {"score": 32, "confidence": "medium"}}
    warnings = watchdog.early_warnings([target], [], scores, TODAY)
    assert warnings == []


def test_window_days_is_read_from_config_not_hardcoded() -> None:
    invoices, promises, scores = _bad_signal_world()
    narrow = copy.deepcopy(rules())
    narrow["early_warning"]["window_days"] = 3          # the invoice is 9 days out
    assert watchdog.early_warnings(invoices, promises, scores, TODAY, config=narrow) == []
    default_result = watchdog.early_warnings(invoices, promises, scores, TODAY)
    assert len(default_result) == 1


def test_band_signal_thresholds_are_read_from_config_not_hardcoded() -> None:
    invoices, promises, scores = _bad_signal_world()
    stricter = copy.deepcopy(rules())
    stricter["early_warning"]["bands"]["watch_from_signals"] = 4   # only 3 categories exist
    assert watchdog.early_warnings(invoices, promises, scores, TODAY, config=stricter) == []


def test_early_warning_never_states_a_legal_fact_or_interest_figure() -> None:
    """Nothing here is legally due yet -- see CLAUDE.md non-negotiable #3."""
    invoices, promises, scores = _bad_signal_world()
    warnings = watchdog.early_warnings(invoices, promises, scores, TODAY)
    citation = re.compile(legal()["citation_pattern"], re.IGNORECASE)

    forbidden_keys = {"interest_paise", "tax_exposure_paise", "facts", "allowed_facts",
                       "available_rung", "interest", "tax"}
    for warning in warnings:
        assert not forbidden_keys & set(warning.keys())
        for reason in warning["reasons"]:
            assert not citation.search(reason), f"legal citation leaked into: {reason!r}"
