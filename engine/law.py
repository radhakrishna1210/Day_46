"""Law engine -- the legal position of the supplier under the MSMED Act.

Pure rules. Every number comes from config/legal.yaml; nothing legal is
hardcoded here and nothing is invented. Simplified for a demo, as of Aug 2026,
not legal advice.

Day 4-5. Law math bugs are disqualifying -- tests come before "done".
"""

from __future__ import annotations

from datetime import date
from typing import Any


def statutory_due_date(invoice: dict[str, Any]) -> date:
    """True due date under Section 15.

    15 days with no written agreement, the agreed term otherwise, capped hard
    at 45 days.
    """
    raise NotImplementedError("step 3: law engine")


def interest_owed_paise(invoice: dict[str, Any], today: date) -> int:
    """Section 16 penal interest to date, in integer paise.

    Compound interest with monthly rests at bank_rate_multiplier times the RBI
    bank rate.
    """
    raise NotImplementedError("step 3: law engine")


def buyer_tax_exposure_paise(invoice: dict[str, Any], today: date) -> int:
    """Section 43B(h): what the delay costs the BUYER in lost deduction, in paise."""
    raise NotImplementedError("step 3: law engine")


def legal_position(invoice: dict[str, Any], today: date) -> dict[str, Any]:
    """The full picture for one invoice.

    Statutory due date, days overdue, interest owed, buyer tax exposure, and
    the highest escalation rung legally available.
    """
    raise NotImplementedError("step 3: law engine")


def samadhaan_draft(invoice: dict[str, Any], buyer: dict[str, Any], today: date) -> str:
    """A ready-to-file MSME Samadhaan complaint draft with the facts filled in."""
    raise NotImplementedError("step 3: law engine")
