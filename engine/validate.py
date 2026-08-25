"""Invoice validation -- catches malformed records before they reach the law
engine or the brain.

Pure rules, no AI. Every check here answers "is this invoice structurally fit
to reason about at all", never "is this a good invoice" -- a legitimate but
disputed, tiny, or nearly-due invoice is not this module's business.

See docs/edge_cases.md:
    TC-045  acceptance_date missing
    TC-049  agreed_days not a number
    TC-050  issue_date in the future
    TC-051  acceptance_date before issue_date (impossible chronology)
    TC-052  duplicate invoice_id
    TC-053  amount is zero
    TC-054  amount is negative

TC-052 is the odd one out: every other check above answers a question about
ONE invoice in isolation, but "is this a duplicate" can only be answered by
looking at the whole batch. See duplicate_reasons() below -- it is not one of
the per-invoice _CHECKS, and invalid_reason() (single invoice) still cannot
see it. reasons_for() is where the two are merged.

An invoice that fails a check here is never silently dropped: the caller
(engine/watchdog.py) excludes it from the work queue so it can never reach
engine/law.py or engine/brain.py, but engine/validate.py itself only ever
reports the reason -- audit.record() and the exceptions list are what make
that exclusion visible, per non-negotiable #1.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine import audit
from engine.config import rules

Check = Any  # a function invoice -> str | None


def _as_date(value: Any) -> date | None:
    """A date if `value` parses cleanly as one, else None. Never raises."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_amount(invoice: dict[str, Any], today: date, bounds: dict[str, Any]) -> str | None:
    """TC-053, TC-054: the amount must be a positive number of paise."""
    amount = invoice.get("amount_paise")
    if not isinstance(amount, int) or amount < int(bounds["min_amount_paise"]):
        return (f"invoice amount ({amount!r} paise) is not a positive figure; "
                f"this is not a valid receivable")
    return None


def _check_acceptance_date(invoice: dict[str, Any], today: date, bounds: dict[str, Any]) -> str | None:
    """TC-045: acceptance_date drives every date in engine/law.py's Section 15/16 math."""
    if _as_date(invoice.get("acceptance_date")) is None:
        return "acceptance date is missing, so the statutory due date cannot be established"
    return None


def _check_agreed_days(invoice: dict[str, Any], today: date, bounds: dict[str, Any]) -> str | None:
    """TC-049: a written agreement with a non-numeric term breaks Section 15's day count."""
    if not invoice.get("written_agreement"):
        return None
    agreed = invoice.get("agreed_days")
    if agreed is None:
        return None
    if isinstance(agreed, bool) or not isinstance(agreed, int):
        return (f"agreed_days ({agreed!r}) is not a number of days, so the agreed "
                f"payment term cannot be computed")
    return None


def _check_chronology(invoice: dict[str, Any], today: date, bounds: dict[str, Any]) -> str | None:
    """TC-051: acceptance cannot happen before the invoice was even issued."""
    issue = _as_date(invoice.get("issue_date"))
    acceptance = _as_date(invoice.get("acceptance_date"))
    if issue is not None and acceptance is not None and acceptance < issue:
        return (f"acceptance date ({acceptance.isoformat()}) is before the invoice date "
                f"({issue.isoformat()}); the chronology is impossible")
    return None


def _check_issue_date_not_future(invoice: dict[str, Any], today: date, bounds: dict[str, Any]) -> str | None:
    """TC-050: an invoice cannot have been issued after today."""
    issue = _as_date(invoice.get("issue_date"))
    if issue is None:
        return None
    grace = timedelta(days=int(bounds["future_issue_date_grace_days"]))
    if issue > today + grace:
        return (f"invoice date ({issue.isoformat()}) is in the future; it cannot yet "
                f"be a real, overdue receivable")
    return None


#: Order matters only in that the FIRST failing check is the one reported --
#: amount first because it needs no date parsing at all, then the checks that
#: build on acceptance_date/issue_date.
_CHECKS: tuple[Check, ...] = (
    _check_amount,
    _check_acceptance_date,
    _check_agreed_days,
    _check_chronology,
    _check_issue_date_not_future,
)


def invalid_reason(invoice: dict[str, Any], today: date, *, config: dict[str, Any] | None = None) -> str | None:
    """A plain-English reason this invoice cannot be reasoned about, or None if it is fit to process.

    Single-invoice only, so this can never catch TC-052 (duplicate invoice_id)
    -- a duplicate is not a property of one record, it is a property of the
    batch. Use reasons_for() when duplicates matter, which is everywhere the
    real pipeline (engine/watchdog.py, sim/run_sim.py) cares about validity.
    """
    bounds = (config or rules())["validation"]
    for check in _CHECKS:
        reason = check(invoice, today, bounds)
        if reason:
            return reason
    return None


def duplicate_reasons(invoices: list[dict[str, Any]]) -> dict[str, str]:
    """TC-052: invoice_id -> reason, for every invoice sharing its invoice_id with another.

    invoice_id is this dataset's only invoice-number field (data/generate.py's
    _assign_ids always hands out unique sequential ones), so a duplicate
    invoice can only mean a literal invoice_id collision here.

    Without this, nothing dedupes by invoice_id anywhere downstream:
    data/store.py returns a plain list, sim/run_sim.py's _totals() and
    verify_conservation() just sum/iterate every entry, and
    report/build_report.py only ever formats numbers already summed upstream.
    A duplicate would be silently double-counted in every headline money
    figure -- both colliding records are flagged here (there is no way to
    tell which one is "real"), which then excludes both from the totals
    (engine/watchdog.py, sim/run_sim.py) rather than guessing.
    """
    counts: dict[str, int] = {}
    for invoice in invoices:
        inv_id = invoice.get("invoice_id")
        if inv_id is not None:
            counts[inv_id] = counts.get(inv_id, 0) + 1
    return {
        invoice["invoice_id"]: (
            f"invoice_id {invoice['invoice_id']!r} appears {counts[invoice['invoice_id']]} "
            f"times in this dataset; a duplicate cannot be safely counted as one receivable"
        )
        for invoice in invoices
        if invoice.get("invoice_id") is not None and counts[invoice["invoice_id"]] > 1
    }


def reasons_for(invoices: list[dict[str, Any]], today: date) -> dict[str, str]:
    """invoice_id -> reason, for every invoice in `invoices` that fails validation."""
    reasons = duplicate_reasons(invoices)
    for invoice in invoices:
        inv_id = invoice["invoice_id"]
        if inv_id in reasons:
            continue  # the duplicate reason already explains it
        reason = invalid_reason(invoice, today)
        if reason:
            reasons[inv_id] = reason
    return reasons


def audit_invalid(invoices: list[dict[str, Any]], today: date, *, log: bool = True) -> dict[str, str]:
    """Find every invalid invoice and, once, record why -- never a silent exclusion.

    Called once per run (at data/world load), not once per simulated day: the
    invoices this checks do not change day to day, and an audit trail that
    repeated the same finding on every one of 120 simulated days would bury
    the signal it exists to preserve.
    """
    reasons = reasons_for(invoices, today)
    if log:
        for invoice in invoices:
            reason = reasons.get(invoice.get("invoice_id"))
            if reason:
                audit.record(
                    invoice_id=invoice["invoice_id"], action="invoice_validation_failed",
                    reason=reason, source="rule", today=today,
                    buyer_id=invoice.get("buyer_id"), actor="validate",
                    detail={"invoice": invoice},
                )
    return reasons
