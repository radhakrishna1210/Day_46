"""Tests for the law engine.

Law math bugs are disqualifying, so these use hand-built invoices with numbers
worked out by hand -- not the generated dataset, which could drift.

Day 3 covers Section 15 only: the statutory due date. Interest and the 43B(h)
tax math land on Day 4 and get their own tests then.

All of this is a simplified reading of the MSMED Act 2006 for a demonstration,
current as of Aug 2026, and is not legal advice.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import law
from engine.config import legal


def invoice(
    *,
    written: bool = True,
    agreed_days: int | None = 45,
    acceptance: str = "2026-01-01",
    agreed_due: str | None = None,
) -> dict:
    return {
        "acceptance_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": agreed_due,
    }


# --- the config is the source of truth -----------------------------------

def test_statutory_windows_come_from_config() -> None:
    """If someone edits legal.yaml, these tests should be what tells them."""
    config = legal()
    assert config["no_agreement_days"] == 15
    assert config["max_agreement_days"] == 45


# --- Section 15: the statutory term --------------------------------------

def test_no_written_agreement_means_fifteen_days() -> None:
    assert law.statutory_term_days(invoice(written=False, agreed_days=None)) == 15


def test_written_agreement_with_no_stated_term_falls_back_to_fifteen() -> None:
    """A written contract that never says when to pay buys no extra time."""
    assert law.statutory_term_days(invoice(written=True, agreed_days=None)) == 15


@pytest.mark.parametrize(("agreed", "expected"), [(15, 15), (30, 30), (44, 44), (45, 45)])
def test_agreed_terms_at_or_under_the_ceiling_are_honoured(agreed: int, expected: int) -> None:
    assert law.statutory_term_days(invoice(agreed_days=agreed)) == expected


@pytest.mark.parametrize("agreed", [46, 60, 90, 120, 365])
def test_agreed_terms_above_the_ceiling_are_void(agreed: int) -> None:
    """Section 15 caps the window at 45 days however long the contract says."""
    assert law.statutory_term_days(invoice(agreed_days=agreed)) == 45


# --- the due date itself --------------------------------------------------

def test_due_date_counts_from_acceptance_not_from_the_invoice_date() -> None:
    """The clock starts when the goods are accepted, not when the bill is raised."""
    assert law.statutory_due_date(invoice(acceptance="2026-01-01", agreed_days=45)) == date(2026, 2, 15)


def test_due_date_with_no_agreement() -> None:
    assert law.statutory_due_date(
        invoice(written=False, agreed_days=None, acceptance="2026-03-10")
    ) == date(2026, 3, 25)


def test_ninety_day_contract_is_still_due_at_forty_five_days() -> None:
    """The headline case: a buyer who signed 90 days is late on day 46."""
    due = law.statutory_due_date(invoice(acceptance="2026-01-01", agreed_days=90))
    assert due == date(2026, 2, 15)


def test_due_date_crosses_a_leap_day_correctly() -> None:
    assert law.statutory_due_date(
        invoice(acceptance="2028-02-01", agreed_days=30)
    ) == date(2028, 3, 2)


def test_accepts_a_date_object_as_well_as_a_string() -> None:
    as_string = law.statutory_due_date(invoice(acceptance="2026-01-01"))
    record = invoice()
    record["acceptance_date"] = date(2026, 1, 1)
    assert law.statutory_due_date(record) == as_string


# --- is the contract term void, and what does that win us? ---------------

@pytest.mark.parametrize(("written", "agreed", "expected"), [
    (True, 90, True),
    (True, 60, True),
    (True, 46, True),
    (True, 45, False),
    (True, 30, False),
    (False, None, False),
    (True, None, False),
])
def test_agreed_term_is_void(written: bool, agreed: int | None, expected: bool) -> None:
    assert law.agreed_term_is_void(invoice(written=written, agreed_days=agreed)) is expected


def test_days_gained_by_law_on_a_ninety_day_contract() -> None:
    """Statutory due 2026-02-15, contract claimed 2026-04-01: 45 days of leverage."""
    record = invoice(acceptance="2026-01-01", agreed_days=90, agreed_due="2026-04-01")
    assert law.days_gained_by_law(record) == 45


def test_days_gained_is_zero_for_a_compliant_contract() -> None:
    record = invoice(acceptance="2026-01-01", agreed_days=30, agreed_due="2026-01-31")
    assert law.days_gained_by_law(record) == 0


def test_days_gained_never_goes_negative() -> None:
    """A contract stricter than the Act does not owe the buyer days back."""
    record = invoice(acceptance="2026-01-01", agreed_days=45, agreed_due="2026-01-10")
    assert law.days_gained_by_law(record) == 0


def test_days_gained_is_zero_when_no_contractual_due_date_exists() -> None:
    assert law.days_gained_by_law(invoice(written=False, agreed_days=None)) == 0


# --- not built yet, and honest about it ----------------------------------

@pytest.mark.parametrize("function_name", [
    "interest_owed_paise",
    "buyer_tax_exposure_paise",
    "legal_position",
])
def test_day_four_functions_are_not_silently_wrong(function_name: str) -> None:
    """Better a NotImplementedError than a plausible number nobody checked."""
    with pytest.raises(NotImplementedError):
        getattr(law, function_name)(invoice(), date(2026, 8, 24))


def test_samadhaan_draft_is_not_built_yet() -> None:
    with pytest.raises(NotImplementedError):
        law.samadhaan_draft(invoice(), {"buyer_id": "BUY-01"}, date(2026, 8, 24))
