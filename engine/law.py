"""Law engine -- the legal position of the supplier under the MSMED Act.

Pure rules. This module contains no legal constants and no legal prose: every
rate, window and sentence comes from config/legal.yaml, and the two policy
thresholds that decide when a fact is worth raising come from config/rules.yaml.
If a figure here is wrong, it is wrong in config and nowhere else.

Simplified for a demonstration, verified as of 23 Aug 2026, not legal advice.

What it computes, and the authority for each:

    Section 15, MSMED Act 2006   the statutory due date. 15 days with no
                                 written agreement; the agreed term otherwise,
                                 capped absolutely at 45 days.
    Section 16, MSMED Act 2006   compound interest with monthly rests at three
                                 times the RBI Bank Rate, running from the day
                                 immediately following the due date, and owed
                                 "notwithstanding anything contained in any
                                 agreement between the buyer and the supplier".
    Section 22                   the unpaid amount and its interest are
                                 disclosable in the buyer's annual accounts.
    Section 23                   that interest is not deductible for the buyer.
    Section 37(2)(g),            a sum payable to a micro or small enterprise
    Income-tax Act 2025          beyond the Section 15 window is deductible only
    (formerly s.43B(h), 1961)    in the year payment is actually made.

Every figure is returned with the inputs that produced it, in `basis`, so an
auditor can re-derive it without reading this file.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from engine.config import legal, rules
from engine.money import format_inr, round_paise


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _as_date(value: date | str) -> date:
    """Accept either a date or an ISO string, always return a date."""
    return value if isinstance(value, date) else date.fromisoformat(value)


def _add_months(day: date, months: int) -> date:
    """Add whole months, clamping to the last valid day.

    A monthly rest falling due on the 31st lands on 28 February in a 28-day
    February, which is how monthly rests are normally read.
    """
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _complete_months(start: date, end: date) -> tuple[int, int]:
    """Whole monthly rests between two dates, plus the leftover stub days."""
    if end <= start:
        return 0, 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if _add_months(start, months) > end:
        months -= 1
    return months, (end - _add_months(start, months)).days


def _payments_upto(invoice: dict[str, Any], today: date) -> list[tuple[date, int]]:
    """Payments received on or before `today`, oldest first.

    The engine never sees the future, even when the record happens to contain
    a payment dated after the day being valued.
    """
    received = [
        (_as_date(payment["date"]), int(payment["amount_paise"]))
        for payment in invoice.get("partial_payments") or []
        if _as_date(payment["date"]) <= today
    ]
    received.sort()
    return received


def outstanding_paise(invoice: dict[str, Any], today: date) -> int:
    """Principal still unpaid as at `today`."""
    paid = sum(amount for _when, amount in _payments_upto(invoice, today))
    return int(invoice["amount_paise"]) - paid


# --------------------------------------------------------------------------
# Section 15 -- the statutory due date
# --------------------------------------------------------------------------

def statutory_term_days(invoice: dict[str, Any]) -> int:
    """Days from acceptance to the statutory due date, under Section 15.

    No written agreement means the short window. With one, the agreed term
    applies -- but the ceiling is absolute, and any term beyond it is void. A
    contract saying 90 days does not buy the buyer 90 days.
    """
    config = legal()
    if not invoice.get("written_agreement") or invoice.get("agreed_days") is None:
        return int(config["no_agreement_days"])
    return min(int(invoice["agreed_days"]), int(config["max_agreement_days"]))


def statutory_due_date(invoice: dict[str, Any]) -> date:
    """The date payment was legally due, counted from acceptance of the goods.

    Section 15 ties the clock to the day of acceptance or deemed acceptance,
    not to the date the invoice was raised.
    """
    return _as_date(invoice["acceptance_date"]) + timedelta(days=statutory_term_days(invoice))


def agreed_term_is_void(invoice: dict[str, Any]) -> bool:
    """True when the contract claimed a term the Act does not allow."""
    agreed = invoice.get("agreed_days")
    if not invoice.get("written_agreement") or agreed is None:
        return False
    return int(agreed) > int(legal()["max_agreement_days"])


def days_gained_by_law(invoice: dict[str, Any]) -> int:
    """How many days earlier the statutory deadline falls than the agreed one.

    Zero when the contract was already compliant. This is the leverage the
    supplier did not know they had.
    """
    agreed_due = invoice.get("agreed_due_date")
    if not agreed_due:
        return 0
    return max(0, (_as_date(agreed_due) - statutory_due_date(invoice)).days)


def days_overdue(invoice: dict[str, Any], today: date) -> int:
    """Days past the statutory due date, floored at zero."""
    return max(0, (today - statutory_due_date(invoice)).days)


# --------------------------------------------------------------------------
# Section 16 -- compound interest with monthly rests
# --------------------------------------------------------------------------

def effective_annual_rate() -> float:
    """Three times the RBI Bank Rate, per Section 16."""
    config = legal()
    return float(config["rbi_bank_rate"]) * int(config["bank_rate_multiplier"])


def monthly_rate() -> float:
    """The rate applied at each monthly rest."""
    return effective_annual_rate() / 12


def interest_start_date(invoice: dict[str, Any]) -> date:
    """Section 16 runs interest from the date IMMEDIATELY FOLLOWING the due date.

    Starting on the due date itself would overstate every invoice by a day.
    """
    return statutory_due_date(invoice) + timedelta(days=1)


def _accrual_factor(start: date, end: date) -> float:
    """Compound over whole months, simple over the trailing stub period.

    The Act says "monthly rests" but prescribes no day-count for an incomplete
    final month; the stub is charged simple interest on the day basis declared
    in config/legal.yaml. That is convention, not statute, and it is recorded
    in the `basis` block of every position.
    """
    if end <= start:
        return 0.0
    months, stub_days = _complete_months(start, end)
    rate = monthly_rate()
    basis = int(legal()["partial_month_day_basis"])
    return (1 + rate) ** months * (1 + rate * stub_days / basis) - 1


def interest_owed_paise(invoice: dict[str, Any], today: date) -> int:
    """Section 16 penal interest accrued to `today`, in integer paise.

    Owed notwithstanding anything the contract says about interest, and payable
    even after the principal has been cleared -- interest that has already
    accrued does not disappear when the invoice is finally paid.

    Partial payments split the accrual into segments, each compounding on the
    balance outstanding during that segment. Interest is not compounded across
    a payment boundary, which understates slightly. That is deliberate: these
    figures get quoted to buyers, so the safe rounding direction is downward.
    """
    start = interest_start_date(invoice)
    if today < start:
        return 0

    payments = _payments_upto(invoice, today)
    balance = int(invoice["amount_paise"]) - sum(
        amount for when, amount in payments if when < start
    )

    total = 0.0
    cursor = start
    for when, amount in payments:
        if when < start:
            continue
        total += balance * _accrual_factor(cursor, when)
        balance -= amount
        cursor = when
    total += balance * _accrual_factor(cursor, today)

    return round_paise(total)


# --------------------------------------------------------------------------
# Section 37(2)(g), Income-tax Act 2025 (formerly s.43B(h), 1961)
# --------------------------------------------------------------------------

def financial_year_end(day: date) -> date:
    """The 31 March at the end of the financial year containing `day`."""
    month, dom = (int(part) for part in str(legal()["financial_year_end"]).split("-"))
    year_end = date(day.year, month, dom)
    return year_end if day <= year_end else date(day.year + 1, month, dom)


def buyer_tax_exposure_paise(invoice: dict[str, Any], today: date) -> int:
    """What the delay costs the BUYER in deferred deduction, in integer paise.

    The disallowance bites when the Section 15 window was missed and the amount
    is still outstanding. Expressed at the assumed tax rate in config, which is
    an assumption about the buyer and not a statutory figure -- messages must
    say "at a 30% rate", never assert it as the buyer's actual rate.
    """
    if days_overdue(invoice, today) <= 0:
        return 0
    outstanding = outstanding_paise(invoice, today)
    if outstanding <= 0:
        return 0
    return round_paise(outstanding * float(legal()["buyer_tax_rate"]))


# --------------------------------------------------------------------------
# The whole picture
# --------------------------------------------------------------------------

def available_rung(invoice: dict[str, Any], today: date, fy_end: date) -> int:
    """The highest escalation rung whose facts are materially true today.

    This says what the law supports, not what we will actually do -- the brain
    owns pacing, via the ladder in config/rules.yaml.
    """
    overdue = days_overdue(invoice, today)
    if overdue <= 0:
        return 1

    gates = rules()["law_gates"]
    rung = 2
    if fy_end < today or (fy_end - today).days <= int(gates["tax_horizon_days"]):
        rung = 3
    if overdue >= int(gates["samadhaan_after_days"]):
        rung = 4
    return rung


def _facts(invoice: dict[str, Any], position: dict[str, Any]) -> list[str]:
    """Render the citable one-liners for this position, from config templates."""
    config = legal()
    templates = config["facts"]
    rung = position["available_rung"]

    if position["days_overdue"] <= 0:
        return []

    values = {
        "no_agreement_days": config["no_agreement_days"],
        "max_agreement_days": config["max_agreement_days"],
        "statutory_term": statutory_term_days(invoice),
        "agreed_days": invoice.get("agreed_days"),
        "statutory_due": position["statutory_due_date"],
        "interest_from": position["interest_from"],
        "effective_rate_pct": f"{effective_annual_rate() * 100:.2f}",
        "bank_rate_pct": f"{float(config['rbi_bank_rate']) * 100:.2f}",
        "interest": format_inr(position["interest_paise"], decimals=True),
        "outstanding": format_inr(position["principal_paise"]),
        "tax_exposure": format_inr(position["tax_exposure_paise"]),
        "tax_rate_pct": f"{float(config['buyer_tax_rate']) * 100:.0f}",
        "tax_provision_current": config["tax_provision_current"],
        "tax_provision_legacy": config["tax_provision_legacy"],
        "fy_end": position["fy_end"],
        "portal_name": config["samadhaan"]["portal_name"],
        "portal_url": config["samadhaan"]["portal_url"],
        "predeposit_pct": f"{float(config['samadhaan']['challenge_predeposit_share']) * 100:.0f}",
    }

    keys = []
    if position["agreed_term_void"]:
        keys.append("section_15_capped")
    elif not invoice.get("written_agreement") or invoice.get("agreed_days") is None:
        keys.append("section_15_no_agreement")
    else:
        keys.append("section_15_agreed")
    keys.append("section_16")

    if rung >= 3:
        keys.append("section_22")
        if config.get("section_23_interest_not_deductible"):
            keys.append("section_23")
        keys.append(
            "tax_deduction_crystallised" if position["tax_deduction_crystallised"]
            else "tax_deduction_upcoming"
        )
    if rung >= 4:
        keys.append("samadhaan")

    return [" ".join(templates[key].format(**values).split()) for key in keys]


def legal_position(invoice: dict[str, Any], today: date) -> dict[str, Any]:
    """The supplier's full legal position on one invoice, as at `today`.

    Returns every figure alongside the inputs that produced it. A disputed
    invoice still accrues interest in law, so it is computed and recorded --
    but `dispute_hold` is set, and no message may quote a held position.
    """
    config = legal()
    due = statutory_due_date(invoice)
    start = interest_start_date(invoice)
    overdue = days_overdue(invoice, today)
    principal = outstanding_paise(invoice, today)
    interest = interest_owed_paise(invoice, today)
    fy_end = financial_year_end(_as_date(invoice["acceptance_date"]))
    months, stub_days = _complete_months(start, today) if today >= start else (0, 0)

    position: dict[str, Any] = {
        "invoice_id": invoice.get("invoice_id"),
        "statutory_due_date": due.isoformat(),
        "interest_from": start.isoformat(),
        "days_overdue": overdue,
        "principal_paise": principal,
        "interest_paise": interest,
        "total_payable_paise": principal + interest,
        "tax_exposure_paise": buyer_tax_exposure_paise(invoice, today),
        "fy_end": fy_end.isoformat(),
        "tax_deduction_crystallised": fy_end < today and overdue > 0 and principal > 0,
        "agreed_term_void": agreed_term_is_void(invoice),
        "days_gained_by_law": days_gained_by_law(invoice),
        "dispute_hold": invoice.get("status") == "disputed" or bool(invoice.get("disputed")),
        "as_of": today.isoformat(),
        "basis": {
            "bank_rate": float(config["rbi_bank_rate"]),
            "multiplier": int(config["bank_rate_multiplier"]),
            "effective_annual_rate": effective_annual_rate(),
            "monthly_rate": monthly_rate(),
            "complete_months": months,
            "stub_days": stub_days,
            "day_basis": int(config["partial_month_day_basis"]),
            "buyer_tax_rate": float(config["buyer_tax_rate"]),
            "statutory_term_days": statutory_term_days(invoice),
            "config_version": config["version"],
            "config_as_of": config["as_of"],
            "config_retrieved_on": config.get("retrieved_on"),
        },
    }
    position["available_rung"] = available_rung(invoice, today, fy_end)
    position["facts"] = _facts(invoice, position)
    return position


def samadhaan_draft(invoice: dict[str, Any], buyer: dict[str, Any], today: date) -> str:
    """A ready-to-file MSME Samadhaan complaint draft with the facts filled in."""
    raise NotImplementedError("step 3: law engine -- Day 5")
