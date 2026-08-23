"""Law engine -- the legal position of the supplier under the MSMED Act.

Pure rules. Every number comes from config/legal.yaml; nothing legal is
hardcoded here and nothing is invented. Simplified for a demo, as of Aug 2026,
not legal advice.

Day 3 implements Section 15 only -- the statutory due date -- because the score
engine and the watchdog both need to measure lateness against the real legal
deadline rather than whatever the contract claimed. Interest, tax exposure and
the Samadhaan draft land on Day 4-5.

Law math bugs are disqualifying, so nothing here ships without tests.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from engine.config import legal


def _as_date(value: date | str) -> date:
    """Accept either a date or an ISO string, always return a date."""
    return value if isinstance(value, date) else date.fromisoformat(value)


def statutory_term_days(invoice: dict[str, Any]) -> int:
    """Days from acceptance to the statutory due date, under Section 15.

    No written agreement means 15 days. With one, the agreed term applies --
    but 45 days is an absolute ceiling, and any term beyond it is void. A
    contract saying 90 days does not buy the buyer 90 days.
    """
    config = legal()
    no_agreement = int(config["no_agreement_days"])
    ceiling = int(config["max_agreement_days"])

    if not invoice.get("written_agreement") or invoice.get("agreed_days") is None:
        return no_agreement
    return min(int(invoice["agreed_days"]), ceiling)


def statutory_due_date(invoice: dict[str, Any]) -> date:
    """The date payment was legally due, counted from acceptance of the goods.

    Note this runs from acceptance, not from the invoice date -- Section 15
    ties the clock to the day of acceptance or deemed acceptance.
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
    gap = (_as_date(agreed_due) - statutory_due_date(invoice)).days
    return max(0, gap)


# --- Day 4-5 ---------------------------------------------------------------

def interest_owed_paise(invoice: dict[str, Any], today: date) -> int:
    """Section 16 penal interest to date, in integer paise.

    Compound interest with monthly rests at bank_rate_multiplier times the RBI
    bank rate.
    """
    raise NotImplementedError("step 3: law engine -- Day 4")


def buyer_tax_exposure_paise(invoice: dict[str, Any], today: date) -> int:
    """Section 43B(h): what the delay costs the BUYER in lost deduction, in paise."""
    raise NotImplementedError("step 3: law engine -- Day 4")


def legal_position(invoice: dict[str, Any], today: date) -> dict[str, Any]:
    """The full picture for one invoice.

    Statutory due date, days overdue, interest owed, buyer tax exposure, and
    the highest escalation rung legally available.
    """
    raise NotImplementedError("step 3: law engine -- Day 4")


def samadhaan_draft(invoice: dict[str, Any], buyer: dict[str, Any], today: date) -> str:
    """A ready-to-file MSME Samadhaan complaint draft with the facts filled in."""
    raise NotImplementedError("step 3: law engine -- Day 5")
