"""Tests for the law engine.

Law math bugs are disqualifying, so these use hand-built invoices with numbers
worked out by hand -- not the generated dataset, which could drift. Every money
assertion below carries the manual arithmetic in a comment so a reviewer can
check it on a calculator without reading the implementation.

All of this is a simplified reading of the MSMED Act 2006 and the Income-tax
Act 2025 for a demonstration, verified as of 23 Aug 2026, and is not legal
advice.

Reference values used throughout (from config/legal.yaml):

    RBI Bank Rate           5.50% p.a.        (verified 2026-08-23)
    Section 16 multiplier   3
    Effective rate          16.50% p.a., compound, monthly rests
    Monthly rate r          0.165 / 12 = 0.01375
    Buyer tax rate          30% (an assumption about the buyer, not statute)
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import law
from engine.config import legal

MONTHLY_RATE = 0.165 / 12          # 0.01375, restated here so the tests are
                                   # independent of the implementation


def invoice(
    *,
    written: bool = True,
    agreed_days: int | None = 45,
    acceptance: str = "2026-01-01",
    agreed_due: str | None = None,
    amount: int = 50_000_000,          # Rs 5,00,000
    status: str = "open",
    payments: list[dict] | None = None,
    paid_date: str | None = None,
) -> dict:
    payments = payments or []
    return {
        "invoice_id": "INV-TEST-0001",
        "buyer_id": "BUY-01",
        "acceptance_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": agreed_due,
        "amount_paise": amount,
        "status": status,
        "partial_payments": payments,
        "amount_paid_paise": sum(p["amount_paise"] for p in payments),
        "paid_date": paid_date,
    }


# ==========================================================================
# Section 15 -- the statutory due date (built on Day 3, unchanged)
# ==========================================================================

def test_statutory_windows_come_from_config() -> None:
    """If someone edits legal.yaml, these tests should be what tells them."""
    config = legal()
    assert config["no_agreement_days"] == 15
    assert config["max_agreement_days"] == 45


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


def test_a_ninety_day_written_term_is_due_at_acceptance_plus_forty_five() -> None:
    """DEFINITION OF DONE (a): the headline case."""
    assert law.statutory_due_date(
        invoice(acceptance="2026-01-01", agreed_days=90)
    ) == date(2026, 2, 15)


def test_no_agreement_is_due_at_acceptance_plus_fifteen() -> None:
    """DEFINITION OF DONE (b)."""
    assert law.statutory_due_date(
        invoice(written=False, agreed_days=None, acceptance="2026-03-10")
    ) == date(2026, 3, 25)


def test_due_date_crosses_a_leap_day_correctly() -> None:
    assert law.statutory_due_date(
        invoice(acceptance="2028-02-01", agreed_days=30)
    ) == date(2028, 3, 2)


def test_accepts_a_date_object_as_well_as_a_string() -> None:
    record = invoice()
    record["acceptance_date"] = date(2026, 1, 1)
    assert law.statutory_due_date(record) == date(2026, 2, 15)


@pytest.mark.parametrize(("written", "agreed", "expected"), [
    (True, 90, True), (True, 60, True), (True, 46, True),
    (True, 45, False), (True, 30, False),
    (False, None, False), (True, None, False),
])
def test_agreed_term_is_void(written: bool, agreed: int | None, expected: bool) -> None:
    assert law.agreed_term_is_void(invoice(written=written, agreed_days=agreed)) is expected


def test_days_gained_by_law_on_a_ninety_day_contract() -> None:
    """Statutory due 2026-02-15, contract claimed 2026-04-01: 45 days of leverage."""
    record = invoice(acceptance="2026-01-01", agreed_days=90, agreed_due="2026-04-01")
    assert law.days_gained_by_law(record) == 45


def test_days_gained_is_zero_for_a_compliant_contract() -> None:
    assert law.days_gained_by_law(
        invoice(acceptance="2026-01-01", agreed_days=30, agreed_due="2026-01-31")
    ) == 0


def test_days_gained_never_goes_negative() -> None:
    assert law.days_gained_by_law(
        invoice(acceptance="2026-01-01", agreed_days=45, agreed_due="2026-01-10")
    ) == 0


# ==========================================================================
# Section 16 -- when interest starts
# ==========================================================================

def test_interest_runs_from_the_day_after_the_due_date() -> None:
    """Section 16: "from the date immediately following the date agreed upon".

    Starting on the due date itself would overstate every invoice by one day.
    """
    record = invoice(acceptance="2026-01-01", agreed_days=45)   # due 2026-02-15
    assert law.interest_start_date(record) == date(2026, 2, 16)


def test_no_interest_before_the_due_date() -> None:
    record = invoice(acceptance="2026-01-01", agreed_days=45)   # due 2026-02-15
    assert law.interest_owed_paise(record, date(2026, 2, 1)) == 0


def test_no_interest_on_the_due_date_itself() -> None:
    """They have the whole of the due date to pay."""
    record = invoice(acceptance="2026-01-01", agreed_days=45)
    assert law.interest_owed_paise(record, date(2026, 2, 15)) == 0


def test_one_day_of_interest() -> None:
    """Rs 5,00,000, one day past due.

    MANUAL MATH
      due 2026-02-15, interest starts 2026-02-16, valued 2026-02-17
      n = 0 complete months, d = 1 day
      factor = (1.01375^0) x (1 + 0.01375 x 1/30) - 1
             = 1 x 1.00045833333 - 1 = 0.00045833333
      interest = 50,000,000 x 0.00045833333 = 22,916.67 paise -> 22,917
               = Rs 229.17
    """
    record = invoice(acceptance="2026-01-01", agreed_days=45)
    assert law.interest_owed_paise(record, date(2026, 2, 17)) == 22_917


# ==========================================================================
# Section 16 -- the compound interest math
# ==========================================================================

def test_six_months_of_compound_interest() -> None:
    """DEFINITION OF DONE (c). Verify this one on a calculator.

    MANUAL MATH
      Invoice   Rs 5,00,000 = 50,000,000 paise
      Accepted  2026-01-01, written agreement, 45 days
      Due       2026-01-01 + 45 = 2026-02-15
      Interest  starts 2026-02-16
      Valued    2026-08-16  ->  exactly 6 complete monthly rests, 0 stub days

      r        = 0.165 / 12            = 0.01375
      1.01375 ^ 6                      = 1.0853884688
      factor   = 1.0853884688 - 1      = 0.0853884688
      interest = 50,000,000 x 0.0853884688
               = 4,269,423.44 paise    -> 4,269,423 paise
               = Rs 42,694.23

      Calculator: 1.01375 [x^y] 6 [=] -1 [x] 500000 [=]  ->  42694.23
    """
    record = invoice(acceptance="2026-01-01", agreed_days=45, amount=50_000_000)
    assert law.interest_owed_paise(record, date(2026, 8, 16)) == 4_269_423


def test_interest_over_complete_months_plus_a_stub_period() -> None:
    """Rs 5,00,000, 2 complete monthly rests plus 20 days.

    MANUAL MATH
      Due 2026-02-15, interest from 2026-02-16, valued 2026-05-06
      rests fall 2026-03-16 and 2026-04-16, then 20 days to 2026-05-06
      n = 2, d = 20
      factor = 1.01375^2 x (1 + 0.01375 x 20/30) - 1
             = 1.0276890625 x 1.00916666667 - 1
             = 1.0371095456 - 1 = 0.0371095456
      interest = 50,000,000 x 0.0371095456 = 1,855,477.28 -> 1,855,477
               = Rs 18,554.77
    """
    record = invoice(acceptance="2026-01-01", agreed_days=45)
    assert law.interest_owed_paise(record, date(2026, 5, 6)) == 1_855_477


def test_monthly_rests_clamp_at_a_short_month_end() -> None:
    """A rest due on the 31st falls on the 28th in a 28-day February.

    MANUAL MATH
      Accepted 2026-01-15, no written agreement -> due 2026-01-30
      Interest from 2026-01-31. One rest lands 2026-02-28 (clamped from the 31st).
      Valued 2026-02-28 -> n = 1, d = 0
      factor = 1.01375 - 1 = 0.01375
      interest = 50,000,000 x 0.01375 = 687,500 paise = Rs 6,875.00
    """
    record = invoice(written=False, agreed_days=None, acceptance="2026-01-15")
    assert law.interest_owed_paise(record, date(2026, 2, 28)) == 687_500


def test_interest_compounds_rather_than_accruing_simply() -> None:
    """Twelve months of compounding must beat twelve months of simple interest.

    simple:   50,000,000 x 0.165           = 8,250,000 paise
    compound: 50,000,000 x (1.01375^12 - 1) = 8,908,... paise
    """
    record = invoice(acceptance="2026-01-01", agreed_days=45)
    compound = law.interest_owed_paise(record, date(2027, 2, 16))
    simple = int(50_000_000 * 0.165)
    assert compound > simple


def test_a_contract_denying_interest_does_not_survive_section_16() -> None:
    """Section 16 applies "notwithstanding anything contained in any agreement".

    The invoice below carries an explicit no-interest clause and a 90-day term.
    Both are ignored: interest accrues from day 46.
    """
    record = invoice(acceptance="2026-01-01", agreed_days=90)
    record["contract_says_no_interest"] = True
    assert law.interest_owed_paise(record, date(2026, 8, 16)) == 4_269_423


# ==========================================================================
# Section 16 -- partial payments reduce the principal
# ==========================================================================

def test_partial_payment_reduces_the_principal_for_interest() -> None:
    """DEFINITION OF DONE (e).

    MANUAL MATH
      Invoice   Rs 10,00,000 = 100,000,000 paise
      Accepted  2026-01-01, NO written agreement -> due 2026-01-16
      Interest  starts 2026-01-17
      Payment   Rs 4,00,000 on 2026-04-17  (exactly 3 rests in)
      Valued    2026-07-17                 (exactly 6 rests in)

      1.01375^3 = 1.0418197871, so each 3-month factor = 0.0418197871

      segment 1  17 Jan -> 17 Apr  on Rs 10,00,000
                 100,000,000 x 0.0418197871 = 4,181,978.71
      segment 2  17 Apr -> 17 Jul  on Rs  6,00,000
                  60,000,000 x 0.0418197871 = 2,509,187.23
                                      total = 6,691,165.94 -> 6,691,166 paise
                                            = Rs 66,911.66
    """
    record = invoice(
        written=False, agreed_days=None, acceptance="2026-01-01", amount=100_000_000,
        payments=[{"date": "2026-04-17", "amount_paise": 40_000_000}],
        status="partially_paid",
    )
    assert law.interest_owed_paise(record, date(2026, 7, 17)) == 6_691_166


def test_the_same_invoice_unpaid_accrues_more_interest() -> None:
    """The counterfactual that proves the payment did something.

    MANUAL MATH
      100,000,000 x (1.01375^6 - 1) = 100,000,000 x 0.0853884688
                                    = 8,538,846.88 -> 8,538,847 paise
                                    = Rs 85,388.47
      The Rs 4,00,000 payment therefore saved Rs 85,388.47 - Rs 66,911.66
                                                          = Rs 18,476.81
    """
    record = invoice(
        written=False, agreed_days=None, acceptance="2026-01-01", amount=100_000_000
    )
    assert law.interest_owed_paise(record, date(2026, 7, 17)) == 8_538_847


def test_a_payment_made_before_the_due_date_reduces_the_starting_principal() -> None:
    """Money received before the clock starts simply lowers what it starts on.

    MANUAL MATH
      Rs 10,00,000, no agreement, accepted 2026-01-01, due 2026-01-16.
      Rs 4,00,000 paid 2026-01-10, before interest starts on 2026-01-17.
      Interest accrues on Rs 6,00,000 for 6 rests to 2026-07-17:
        60,000,000 x 0.0853884688 = 5,123,308.13 -> 5,123,308 paise
    """
    record = invoice(
        written=False, agreed_days=None, acceptance="2026-01-01", amount=100_000_000,
        payments=[{"date": "2026-01-10", "amount_paise": 40_000_000}],
        status="partially_paid",
    )
    assert law.interest_owed_paise(record, date(2026, 7, 17)) == 5_123_308


def test_interest_survives_the_principal_being_paid_in_full() -> None:
    """Section 16 interest that has already accrued is still owed after payment."""
    record = invoice(
        written=False, agreed_days=None, acceptance="2026-01-01", amount=100_000_000,
        payments=[{"date": "2026-04-17", "amount_paise": 100_000_000}],
        status="paid", paid_date="2026-04-17",
    )
    # 100,000,000 x 0.0418197871 = 4,181,978.71 -> 4,181,979 paise
    assert law.interest_owed_paise(record, date(2026, 7, 17)) == 4_181_979


def test_a_payment_dated_after_today_is_ignored() -> None:
    """The engine must never see the future, even if the record contains it."""
    record = invoice(
        written=False, agreed_days=None, acceptance="2026-01-01", amount=100_000_000,
        payments=[{"date": "2026-06-01", "amount_paise": 40_000_000}],
        status="partially_paid",
    )
    at_may = law.interest_owed_paise(record, date(2026, 5, 17))
    # 4 rests on the full principal: 100,000,000 x (1.01375^4 - 1)
    #   1.01375^4 = 1.0561448088 -> 0.0561448088 -> 5,614,480.88 -> 5,614,481
    assert at_may == 5_614_481


# ==========================================================================
# The interest figure is driven by config, not by constants in the code
# ==========================================================================

def test_interest_tracks_the_configured_bank_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Double the bank rate in config and the arithmetic must follow.

    This is the guard behind "no legal constants outside config/legal.yaml":
    if 0.055 were hardcoded anywhere in engine/law.py, this test fails.

    MANUAL MATH at 11.00% bank rate
      effective = 0.11 x 3 = 0.33, monthly r = 0.0275
      1.0275^6 = 1.1769... ; factor = 0.1769...
      Only the direction is asserted, plus an independent recomputation.
    """
    doubled = dict(legal())
    doubled["rbi_bank_rate"] = 0.11
    monkeypatch.setattr(law, "legal", lambda: doubled)

    record = invoice(acceptance="2026-01-01", agreed_days=45)
    higher = law.interest_owed_paise(record, date(2026, 8, 16))

    expected = round(50_000_000 * ((1 + 0.33 / 12) ** 6 - 1))
    assert higher == expected
    assert higher > 4_269_423


def test_interest_tracks_the_configured_multiplier(monkeypatch: pytest.MonkeyPatch) -> None:
    single = dict(legal())
    single["bank_rate_multiplier"] = 1
    monkeypatch.setattr(law, "legal", lambda: single)

    record = invoice(acceptance="2026-01-01", agreed_days=45)
    expected = round(50_000_000 * ((1 + 0.055 / 12) ** 6 - 1))
    assert law.interest_owed_paise(record, date(2026, 8, 16)) == expected


# ==========================================================================
# Section 37(2)(g) / 43B(h) -- the buyer's own tax cost
# ==========================================================================

def test_tax_exposure_is_the_outstanding_amount_times_the_tax_rate() -> None:
    """DEFINITION OF DONE (d).

    MANUAL MATH
      Rs 5,00,000 unpaid, accepted 2025-06-01, no agreement -> due 2025-06-16.
      The financial year containing acceptance ended 2026-03-31, already past.
      exposure = 50,000,000 x 0.30 = 15,000,000 paise = Rs 1,50,000
    """
    record = invoice(written=False, agreed_days=None, acceptance="2025-06-01")
    assert law.buyer_tax_exposure_paise(record, date(2026, 8, 16)) == 15_000_000


def test_tax_exposure_uses_the_outstanding_balance_not_the_face_value() -> None:
    """Rs 10,00,000 with Rs 4,00,000 paid: exposure is on the Rs 6,00,000 left.

    60,000,000 x 0.30 = 18,000,000 paise = Rs 1,80,000
    """
    record = invoice(
        written=False, agreed_days=None, acceptance="2025-06-01", amount=100_000_000,
        payments=[{"date": "2025-09-01", "amount_paise": 40_000_000}],
        status="partially_paid",
    )
    assert law.buyer_tax_exposure_paise(record, date(2026, 8, 16)) == 18_000_000


def test_no_tax_exposure_before_the_statutory_window_is_missed() -> None:
    """Pay inside the window and the deduction is never at risk."""
    record = invoice(written=False, agreed_days=None, acceptance="2026-08-10")
    assert law.buyer_tax_exposure_paise(record, date(2026, 8, 16)) == 0


def test_no_tax_exposure_once_the_invoice_is_settled() -> None:
    record = invoice(
        written=False, agreed_days=None, acceptance="2025-06-01",
        payments=[{"date": "2025-09-01", "amount_paise": 50_000_000}],
        status="paid", paid_date="2025-09-01",
    )
    assert law.buyer_tax_exposure_paise(record, date(2026, 8, 16)) == 0


def test_financial_year_end_follows_the_acceptance_date() -> None:
    assert law.financial_year_end(date(2026, 6, 1)) == date(2027, 3, 31)
    assert law.financial_year_end(date(2026, 3, 30)) == date(2026, 3, 31)
    assert law.financial_year_end(date(2026, 4, 1)) == date(2027, 3, 31)
    assert law.financial_year_end(date(2027, 1, 15)) == date(2027, 3, 31)


def test_deduction_is_crystallised_only_after_the_year_end_passes() -> None:
    """Before 31 March the buyer can still avoid the whole thing by paying."""
    upcoming = invoice(written=False, agreed_days=None, acceptance="2026-06-01")
    position = law.legal_position(upcoming, date(2026, 8, 16))
    assert position["fy_end"] == "2027-03-31"
    assert position["tax_deduction_crystallised"] is False
    assert position["tax_exposure_paise"] == 15_000_000

    passed = invoice(written=False, agreed_days=None, acceptance="2025-06-01")
    position = law.legal_position(passed, date(2026, 8, 16))
    assert position["tax_deduction_crystallised"] is True


def test_tax_exposure_tracks_the_configured_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    lower = dict(legal())
    lower["buyer_tax_rate"] = 0.25
    monkeypatch.setattr(law, "legal", lambda: lower)
    record = invoice(written=False, agreed_days=None, acceptance="2025-06-01")
    assert law.buyer_tax_exposure_paise(record, date(2026, 8, 16)) == 12_500_000


# ==========================================================================
# legal_position -- the whole picture
# ==========================================================================

def overdue_invoice() -> dict:
    """Rs 5,00,000, 90-day contract, accepted 2026-01-01. Due 2026-02-15."""
    return invoice(acceptance="2026-01-01", agreed_days=90, agreed_due="2026-04-01")


def test_legal_position_reports_every_number_the_plan_promised() -> None:
    position = law.legal_position(overdue_invoice(), date(2026, 8, 16))
    assert position["statutory_due_date"] == "2026-02-15"
    assert position["interest_from"] == "2026-02-16"
    assert position["days_overdue"] == 182
    assert position["principal_paise"] == 50_000_000
    assert position["interest_paise"] == 4_269_423
    assert position["total_payable_paise"] == 54_269_423
    assert position["agreed_term_void"] is True
    assert position["days_gained_by_law"] == 45


def test_total_payable_is_principal_plus_interest() -> None:
    position = law.legal_position(overdue_invoice(), date(2026, 8, 16))
    assert (
        position["total_payable_paise"]
        == position["principal_paise"] + position["interest_paise"]
    )


def test_legal_position_records_its_own_inputs_for_the_audit_trail() -> None:
    """Non-negotiable #1: an auditor must be able to re-derive every rupee."""
    basis = law.legal_position(overdue_invoice(), date(2026, 8, 16))["basis"]
    assert basis["bank_rate"] == 0.055
    assert basis["multiplier"] == 3
    assert basis["effective_annual_rate"] == pytest.approx(0.165)
    assert basis["monthly_rate"] == pytest.approx(0.01375)
    assert basis["complete_months"] == 6
    assert basis["stub_days"] == 0
    assert basis["day_basis"] == 30
    assert basis["buyer_tax_rate"] == 0.30
    assert basis["config_as_of"] == "2026-08"


def test_a_position_on_an_invoice_not_yet_due_is_all_zeros() -> None:
    position = law.legal_position(
        invoice(acceptance="2026-08-10", written=False, agreed_days=None),
        date(2026, 8, 16),
    )
    assert position["days_overdue"] == 0
    assert position["interest_paise"] == 0
    assert position["tax_exposure_paise"] == 0
    assert position["available_rung"] == 1


# --- the escalation rung the law supports --------------------------------

def test_rung_one_before_the_due_date() -> None:
    record = invoice(acceptance="2026-08-10", written=False, agreed_days=None)
    assert law.legal_position(record, date(2026, 8, 16))["available_rung"] == 1


def test_rung_two_when_only_interest_has_started() -> None:
    """Overdue 10 days, financial year end still far away."""
    record = invoice(acceptance="2026-05-16", written=False, agreed_days=None)
    assert law.legal_position(record, date(2026, 6, 10))["available_rung"] == 2


def test_rung_three_when_the_tax_year_end_comes_into_range() -> None:
    """Overdue 10 days, but 31 March is 49 days away -- the deduction is at risk."""
    record = invoice(acceptance="2027-01-16", written=False, agreed_days=None)
    assert law.legal_position(record, date(2027, 2, 10))["available_rung"] == 3


def test_rung_four_once_the_delay_is_long_enough_for_samadhaan() -> None:
    record = invoice(acceptance="2026-04-06", written=False, agreed_days=None)
    assert law.legal_position(record, date(2026, 6, 10))["available_rung"] == 4


# --- the facts we are allowed to state ------------------------------------

def test_facts_are_stated_and_cited() -> None:
    facts = law.legal_position(overdue_invoice(), date(2026, 8, 16))["facts"]
    joined = " ".join(facts)
    assert "Section 15" in joined
    assert "Section 16" in joined
    assert "Section 22" in joined
    assert "Section 23" in joined
    assert "Section 37(2)(g)" in joined


def test_facts_quote_the_numbers_the_engine_computed() -> None:
    position = law.legal_position(overdue_invoice(), date(2026, 8, 16))
    joined = " ".join(position["facts"])
    assert "16.50" in joined        # effective rate
    assert "5.50" in joined         # bank rate
    assert "42,694.23" in joined    # interest to date, Indian grouping
    assert "45" in joined           # the ceiling the contract broke


def test_facts_never_threaten() -> None:
    """Non-negotiable #3: messages state facts. This is the last line of defence."""
    banned = (
        "legal action", "we will sue", "court", "blacklist", "penalty will be",
        "consequences", "failure to comply", "immediately or", "warn",
    )
    facts = law.legal_position(overdue_invoice(), date(2026, 8, 16))["facts"]
    for fact in facts:
        lowered = fact.lower()
        for word in banned:
            assert word not in lowered, f"threatening language in fact: {fact}"


def test_fact_text_lives_in_config_not_in_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-negotiable #3: legal prose is config, so it can be reviewed in one place."""
    edited = dict(legal())
    edited["facts"] = dict(edited["facts"])
    edited["facts"]["section_22"] = "SENTINEL disclosure line."
    monkeypatch.setattr(law, "legal", lambda: edited)

    facts = law.legal_position(overdue_invoice(), date(2026, 8, 16))["facts"]
    assert any("SENTINEL" in fact for fact in facts)


# --- disputes -------------------------------------------------------------

def test_a_disputed_invoice_still_accrues_interest_but_is_held() -> None:
    """Interest accrues in law regardless of the dispute.

    We compute and record it so the audit trail is complete, and set
    dispute_hold so no message may quote it. Chasing a disputed invoice is
    how a supplier loses a customer.
    """
    record = overdue_invoice()
    record["status"] = "disputed"
    position = law.legal_position(record, date(2026, 8, 16))
    assert position["interest_paise"] == 4_269_423
    assert position["dispute_hold"] is True


def test_an_undisputed_invoice_is_not_held() -> None:
    position = law.legal_position(overdue_invoice(), date(2026, 8, 16))
    assert position["dispute_hold"] is False


# ==========================================================================
# Still to come
# ==========================================================================

def test_samadhaan_draft_is_not_built_yet() -> None:
    with pytest.raises(NotImplementedError):
        law.samadhaan_draft(invoice(), {"buyer_id": "BUY-01"}, date(2026, 8, 24))
