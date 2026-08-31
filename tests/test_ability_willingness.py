"""Tests for the two-axis buyer score.

The load-bearing test here is the first one: this phase promised to be purely
additive, so engine.score.score_buyer()'s record must come out of
two_axis_score() byte-for-byte identical to what score_buyer() returns on its
own. Everything else in the agent -- brain, writer, watchdog, buyer_panel,
the simulator -- reads that record, and none of it knows this module exists.

The second thing worth stating: ability and willingness must actually
DISAGREE for the split to be worth anything. A buyer who is broke and a buyer
who is stalling both look bad to the legacy score; the tests below pin that
they land in different quadrants.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from engine import ability_willingness as aw
from engine import score
from engine.config import rules

TODAY = date(2026, 8, 24)


def paid(delay_days: int, *, days_ago: int = 30, promise_broken: bool = False,
         disputed: bool = False, agreed_days: int | None = 45) -> dict:
    """A settled invoice paid `delay_days` after its statutory due date."""
    paid_date = TODAY - timedelta(days=days_ago)
    acceptance = paid_date - timedelta(days=(agreed_days or 15) + delay_days)
    return {
        "invoice_id": f"INV-{days_ago:04d}-{delay_days}",
        "buyer_id": "BUY-01",
        "status": "paid",
        "acceptance_date": acceptance.isoformat(),
        "paid_date": paid_date.isoformat(),
        "written_agreement": agreed_days is not None,
        "agreed_days": agreed_days,
        "agreed_due_date": None,
        "promise_broken": promise_broken,
        "disputed": disputed,
    }


def buyer(inflow: list[int] | None, failed: int = 0) -> dict:
    """A buyer record carrying whatever inflow evidence a test needs."""
    record = {"buyer_id": "BUY-01", "name": "Test Traders", "profile": "small_trader"}
    if inflow is not None:
        record["monthly_inflow_paise"] = inflow
    record["failed_payment_count"] = failed
    return record


#: A steady, healthy month-in-month-out inflow: no trend, no volatility.
HEALTHY = [50_000_000] * 8
#: The same buyer's money falling away month by month.
DECLINING = [80_000_000, 74_000_000, 68_000_000, 60_000_000,
             52_000_000, 44_000_000, 36_000_000, 30_000_000]


# --- the legacy record must survive untouched -----------------------------

def test_the_two_axis_record_carries_the_legacy_score_record_unchanged() -> None:
    """The whole phase rests on this: everything else reads score_buyer()."""
    invoices = [paid(12), paid(8, promise_broken=True), paid(30)]
    legacy = score.score_buyer(buyer(HEALTHY), invoices, TODAY)
    combined = aw.two_axis_score(buyer(HEALTHY), invoices, TODAY)

    for key, value in legacy.items():
        assert combined[key] == value, f"two_axis_score changed the legacy {key!r}"


def test_the_two_axis_record_adds_exactly_three_keys_and_no_others() -> None:
    invoices = [paid(12), paid(8)]
    legacy = set(score.score_buyer(buyer(HEALTHY), invoices, TODAY))
    combined = set(aw.two_axis_score(buyer(HEALTHY), invoices, TODAY))
    assert combined - legacy == {"ability", "willingness", "quadrant"}


def test_willingness_is_an_exact_relabel_of_the_legacy_score() -> None:
    """Shipped config gives willingness the legacy weights, so the two agree.

    Pinned deliberately: if a future phase tunes them apart, that has to be a
    decision somebody made, not a drift nobody noticed.
    """
    for invoices in ([paid(0), paid(0), paid(0)],
                     [paid(10), paid(10), paid(10, promise_broken=True)],
                     [paid(90), paid(60, disputed=True)],
                     []):
        combined = aw.two_axis_score(buyer(HEALTHY), invoices, TODAY)
        assert combined["willingness"]["score"] == combined["score"]


# --- the axes have to disagree to be worth anything -----------------------

def test_a_buyer_with_declining_inflow_and_kept_promises_is_a_cash_flow_problem() -> None:
    """Wants to pay, cannot. The legacy score alone cannot see the difference."""
    combined = aw.two_axis_score(buyer(DECLINING, failed=3), [paid(0), paid(0), paid(0)], TODAY)
    assert combined["willingness"]["score"] >= rules()["score"]["quadrant"]["willingness_high_from"]
    assert combined["ability"]["score"] < rules()["score"]["quadrant"]["ability_high_from"]
    assert combined["quadrant"] == aw.CASH_FLOW_PROBLEM


def test_a_buyer_with_healthy_inflow_and_broken_promises_can_pay_but_wont() -> None:
    """Could pay, chooses not to -- the case the legacy score cannot isolate."""
    history = [paid(40, promise_broken=True), paid(50, promise_broken=True),
               paid(45, promise_broken=True)]
    combined = aw.two_axis_score(buyer(HEALTHY), history, TODAY)
    assert combined["ability"]["score"] >= rules()["score"]["quadrant"]["ability_high_from"]
    assert combined["willingness"]["score"] < rules()["score"]["quadrant"]["willingness_high_from"]
    assert combined["quadrant"] == aw.CAN_PAY_BUT_WONT


def test_the_same_payment_record_splits_into_two_different_quadrants() -> None:
    """Identical history, different money coming in -- opposite prescriptions.

    This is the phase's entire thesis in one assertion: the legacy score is
    the same number for both buyers, and it is the wrong number to act on.
    """
    history = [paid(40, promise_broken=True), paid(35), paid(45)]
    broke = aw.two_axis_score(buyer(DECLINING, failed=4), history, TODAY)
    flush = aw.two_axis_score(buyer(HEALTHY), history, TODAY)

    assert broke["score"] == flush["score"]              # the legacy view: identical
    assert broke["quadrant"] != flush["quadrant"]        # the two-axis view: not at all


# --- quadrant boundaries --------------------------------------------------

def test_the_quadrant_threshold_value_itself_counts_as_high_on_both_axes() -> None:
    config = rules()["score"]["quadrant"]
    able, willing = int(config["ability_high_from"]), int(config["willingness_high_from"])
    assert aw.quadrant(able, willing) == aw.GOOD_CUSTOMER
    assert aw.quadrant(able - 1, willing) == aw.CASH_FLOW_PROBLEM
    assert aw.quadrant(able, willing - 1) == aw.CAN_PAY_BUT_WONT
    assert aw.quadrant(able - 1, willing - 1) == aw.HIGH_RISK


@pytest.mark.parametrize(
    ("ability_score", "willingness_score", "expected"),
    [
        (100, 100, aw.GOOD_CUSTOMER),
        (0, 100, aw.CASH_FLOW_PROBLEM),
        (100, 0, aw.CAN_PAY_BUT_WONT),
        (0, 0, aw.HIGH_RISK),
    ],
)
def test_every_corner_of_the_grid_maps_to_its_named_quadrant(
    ability_score: int, willingness_score: int, expected: str,
) -> None:
    assert aw.quadrant(ability_score, willingness_score) == expected


def test_every_quadrant_the_function_can_return_has_a_plain_english_meaning() -> None:
    assert set(aw.QUADRANTS) == set(aw.QUADRANT_MEANING)


# --- ability, factor by factor --------------------------------------------

def test_a_buyer_with_no_inflow_history_scores_the_neutral_base_and_says_so() -> None:
    """Degrades the way confidence already does: admits ignorance, invents nothing."""
    record = aw.ability(buyer(None))
    assert record["score"] == int(rules()["score"]["ability"]["base"])
    assert record["signals"]["months_of_inflow"] == 0
    assert record["signals"]["typical_monthly_inflow_paise"] is None
    assert any(item["factor"] == "no inflow data" for item in record["breakdown"])


def test_an_empty_inflow_series_is_treated_as_no_data_rather_than_as_zero_income() -> None:
    assert aw.ability(buyer([]))["score"] == int(rules()["score"]["ability"]["base"])


def test_a_declining_inflow_series_scores_lower_ability_than_a_steady_one() -> None:
    assert aw.ability(buyer(DECLINING))["score"] < aw.ability(buyer(HEALTHY))["score"]


def test_failed_payments_cost_the_configured_points_each() -> None:
    weight = float(rules()["score"]["ability"]["weights"]["failed_payment"])
    clean = aw.ability(buyer(HEALTHY, failed=0))["score"]
    one_bounce = aw.ability(buyer(HEALTHY, failed=1))["score"]
    assert clean - one_bounce == pytest.approx(weight, abs=1)


def test_a_bigger_invoice_scores_lower_ability_than_a_smaller_one_for_the_same_buyer() -> None:
    """Ability is about paying THIS invoice, not about the buyer in the abstract."""
    small = aw.ability(buyer(HEALTHY), invoice_paise=1_000_000)
    large = aw.ability(buyer(HEALTHY), invoice_paise=100_000_000)
    assert large["score"] < small["score"]
    assert large["signals"]["invoice_to_capacity_ratio"] > small["signals"]["invoice_to_capacity_ratio"]


def test_ability_for_invoice_sizes_the_ratio_against_what_is_still_owed() -> None:
    """A part-paid invoice is a smaller ask than its face value."""
    invoice = {"amount_paise": 40_000_000, "amount_paid_paise": 30_000_000}
    record = aw.ability_for_invoice(buyer(HEALTHY), invoice)
    typical = record["signals"]["typical_monthly_inflow_paise"]
    assert record["signals"]["invoice_to_capacity_ratio"] == round(10_000_000 / typical, 2)


def test_an_invoice_is_never_treated_as_owing_a_negative_amount() -> None:
    overpaid = {"amount_paise": 10_000_000, "amount_paid_paise": 12_000_000}
    assert aw.outstanding_paise(overpaid) == 0


def test_ability_is_clamped_into_the_configured_range() -> None:
    config = rules()["score"]["ability"]
    ruinous = aw.ability(buyer(DECLINING, failed=50), invoice_paise=10_000_000_000)
    assert record_in_range(ruinous, config)
    pristine = aw.ability(buyer([10_000_000, 90_000_000] * 4))
    assert record_in_range(pristine, config)


def record_in_range(record: dict, config: dict) -> bool:
    return int(config["min"]) <= record["score"] <= int(config["max"])


def test_a_trend_is_not_called_from_too_few_months() -> None:
    """Same refusal engine/score.py's trend makes: two points is not a trend."""
    minimum = int(rules()["score"]["ability"]["min_months_for_trend"])
    assert aw.inflow_trend_pct([50_000_000] * (minimum - 1)) is None
    assert aw.inflow_trend_pct([50_000_000] * minimum) is not None


def test_a_short_inflow_series_still_reports_a_typical_month() -> None:
    """Too short to trend is not too short to size an invoice against."""
    assert aw.typical_monthly_inflow_paise([50_000_000]) == 50_000_000


def test_the_typical_month_is_a_median_so_one_freak_month_cannot_redefine_capacity() -> None:
    recent = int(rules()["score"]["ability"]["recent_months"])
    series = [10_000_000] * 5 + [10_000_000] * (recent - 1) + [900_000_000]
    assert aw.typical_monthly_inflow_paise(series) == 10_000_000


def test_a_malformed_inflow_series_degrades_instead_of_raising() -> None:
    """Nothing in the buyer record is trusted to be well-formed."""
    assert aw.inflow_series({"monthly_inflow_paise": "not a list"}) == []
    assert aw.inflow_series({"monthly_inflow_paise": [1_000, None, -5, 2_000]}) == [1_000, 2_000]
    assert aw.failed_payment_count({"failed_payment_count": -3}) == 0
    assert aw.failed_payment_count({"failed_payment_count": "two"}) == 0


# --- explanations ---------------------------------------------------------

def test_explain_ability_mentions_every_factor_that_produced_it() -> None:
    combined = aw.two_axis_score(buyer(DECLINING, failed=2), [paid(12)], TODAY)
    text = aw.explain_ability(combined)
    assert text.strip()
    for item in combined["ability"]["breakdown"]:
        assert item["factor"] in text


def test_explain_willingness_mentions_every_factor_that_produced_it() -> None:
    combined = aw.two_axis_score(buyer(HEALTHY), [paid(12), paid(20, promise_broken=True)], TODAY)
    text = aw.explain_willingness(combined)
    assert text.strip()
    for item in combined["willingness"]["breakdown"]:
        assert item["factor"] in text


def test_both_explanations_name_the_quadrant_and_what_it_means() -> None:
    combined = aw.two_axis_score(buyer(DECLINING, failed=2), [paid(0), paid(0), paid(0)], TODAY)
    for text in (aw.explain_ability(combined), aw.explain_willingness(combined)):
        assert combined["quadrant"] in text
        assert aw.QUADRANT_MEANING[combined["quadrant"]] in text


def test_the_breakdown_arithmetic_adds_up_to_the_ability_score() -> None:
    """Same guarantee the legacy score gives: the shown working is the answer."""
    record = aw.ability(buyer(DECLINING, failed=2), invoice_paise=5_000_000)
    assert sum(item["points"] for item in record["breakdown"]) == pytest.approx(
        record["score"], abs=0.5)


# --- the Brain must not have started reading any of this yet --------------

def test_the_brain_now_reads_the_two_axis_score_as_of_phase_3() -> None:
    """Phase 1 was compute-and-explain only; Phase 3 is exactly the phase
    that wires the quadrant into engine/brain.py.

    The inverse of the Phase 1-2 tripwire this replaces: that test asserted
    engine/brain.py never mentioned "quadrant"; this one marks that the
    wiring has actually landed, so a later regression that quietly drops the
    EV branch (rather than deliberately removing it) gets caught here too.
    """
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "engine" / "brain.py"
    text = source.read_text(encoding="utf-8")
    for marker in ("ability_willingness", "quadrant"):
        assert marker in text, f"engine/brain.py no longer references {marker!r}"
