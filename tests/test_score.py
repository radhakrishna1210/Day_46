"""Tests for the buyer score.

The score decides how hard the agent chases someone for money, so the
arithmetic is pinned against hand-built histories rather than the generated
dataset. Weights come from config/rules.yaml; if someone retunes them, the
tests that assert exact totals are meant to fail and be re-read.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from engine import score
from engine.config import rules

TODAY = date(2026, 8, 24)


def paid(delay_days: int, *, days_ago: int = 30, promise_broken: bool = False,
         disputed: bool = False, agreed_days: int | None = 45) -> dict:
    """A settled invoice paid `delay_days` after its statutory due date.

    Negative delay means paid early. `days_ago` places the payment relative to
    TODAY so the trend window can be exercised.
    """
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


BUYER = {"buyer_id": "BUY-01", "name": "Test Traders"}


def score_of(invoices: list[dict], today: date = TODAY) -> dict:
    return score.score_buyer(BUYER, invoices, today)


# --- the measurement itself ----------------------------------------------

def test_delay_is_measured_against_the_statutory_date_not_the_contract() -> None:
    """A buyer who used all 90 contracted days is 45 days late, and is scored so."""
    invoice = paid(delay_days=0, agreed_days=90)
    # paid() built this to land exactly on the 90-day mark from acceptance
    assert score.payment_delay_days(invoice) == 45


def test_paying_early_gives_a_negative_delay() -> None:
    assert score.payment_delay_days(paid(delay_days=-5)) == -5


def test_unpaid_invoices_are_not_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invoice still running says nothing about the buyer yet."""
    open_invoice = dict(paid(0), status="open", paid_date=None)
    result = score_of([paid(0), paid(0), open_invoice])
    assert result["history_count"] == 2


def test_future_payments_are_excluded() -> None:
    """The clock is passed in, so history means history as of that date."""
    result = score_of([paid(0, days_ago=10), paid(0, days_ago=-5)])
    assert result["history_count"] == 1


# --- the formula ----------------------------------------------------------

def test_a_spotless_record_scores_full_marks() -> None:
    result = score_of([paid(0), paid(0), paid(0)])
    assert result["score"] == 100
    assert result["signals"]["on_time_streak"] == 3


def test_known_arithmetic() -> None:
    """100 - (10 avg delay x 1.2) - (1 broken x 8) = 80, no streak, no disputes."""
    weights = rules()["score"]["weights"]
    assert (weights["avg_delay_penalty"], weights["broken_promise_penalty"]) == (1.2, 8)

    history = [paid(10), paid(10), paid(10, promise_broken=True)]
    result = score_of(history)
    assert result["signals"]["average_delay_days"] == 10.0
    assert result["signals"]["broken_promises"] == 1
    assert result["score"] == 80


def test_disputes_cost_five_points_each() -> None:
    result = score_of([paid(0, disputed=True), paid(0, disputed=True), paid(0)])
    # three on-time invoices: 100 + 3x2 streak bonus, clamped, minus 2x5 disputes
    assert result["signals"]["disputes_raised"] == 2
    assert result["score"] == 96


def test_score_is_clamped_at_zero() -> None:
    result = score_of([paid(200), paid(200, promise_broken=True)])
    assert result["score"] == 0
    assert any(item["factor"] == "clamped" for item in result["breakdown"])


def test_score_is_clamped_at_one_hundred() -> None:
    result = score_of([paid(-5) for _ in range(10)])
    assert result["score"] == 100


def test_breakdown_arithmetic_adds_up_to_the_score() -> None:
    """Every point of every score has to be traceable, or it is not explainable."""
    for history in ([paid(10), paid(30, promise_broken=True)], [paid(0)], [paid(200)] * 3):
        result = score_of(history)
        total = sum(item["points"] for item in result["breakdown"])
        assert round(total) == result["score"]


# --- streaks --------------------------------------------------------------

def test_streak_counts_only_the_most_recent_run() -> None:
    history = [paid(0, days_ago=10), paid(0, days_ago=20), paid(40, days_ago=30), paid(0, days_ago=40)]
    assert score.on_time_streak(score.settled_history(history, TODAY)) == 2


def test_streak_is_zero_when_the_latest_invoice_was_late() -> None:
    history = [paid(5, days_ago=10), paid(0, days_ago=20), paid(0, days_ago=30)]
    assert score.on_time_streak(score.settled_history(history, TODAY)) == 0


def test_paying_exactly_on_the_due_date_counts_as_on_time() -> None:
    assert score.on_time_streak(score.settled_history([paid(0)], TODAY)) == 1


# --- confidence -----------------------------------------------------------

@pytest.mark.parametrize(("count", "expected"), [
    (0, "low"), (1, "low"), (2, "low"),
    (3, "medium"), (5, "medium"), (9, "medium"),
    (10, "high"), (25, "high"),
])
def test_confidence_thresholds(count: int, expected: str) -> None:
    assert score.confidence(count) == expected


def test_a_buyer_with_no_history_is_flagged_not_trusted() -> None:
    """docs/edge_cases.md TC-064 (zero payment history -> low confidence).

    The neutral default is 100, which would be dangerous read on its own.
    """
    result = score_of([])
    assert result["score"] == 100
    assert result["confidence"] == "low"
    assert result["history_count"] == 0
    assert any(item["factor"] == "no history" for item in result["breakdown"])


def test_a_buyer_with_one_prior_invoice_is_still_low_confidence() -> None:
    """docs/edge_cases.md TC-065: one prior invoice is not enough evidence
    either. test_confidence_thresholds[1-low] already pins confidence() in
    isolation; this is the same case at the score_of() level, the way TC-064
    is pinned above -- one settled invoice is real evidence (unlike TC-064's
    empty history), and it still is not enough to be trusted on its own.
    """
    result = score_of([paid(0)])
    assert result["history_count"] == 1
    assert result["confidence"] == "low"


# --- trend ----------------------------------------------------------------

def test_trend_is_unknown_without_enough_old_history() -> None:
    result = score_of([paid(0, days_ago=10), paid(0, days_ago=20)])
    assert result["trend"]["direction"] == "unknown"
    assert result["trend"]["earlier_score"] is None


def test_trend_detects_a_buyer_getting_worse() -> None:
    old = [paid(0, days_ago=300), paid(0, days_ago=280), paid(0, days_ago=260)]
    recent = [paid(60, days_ago=30), paid(70, days_ago=20), paid(80, days_ago=10)]
    result = score_of(old + recent)
    assert result["trend"]["direction"] == "worsening"
    assert result["trend"]["earlier_score"] > result["score"]


def test_trend_detects_a_buyer_getting_better() -> None:
    old = [paid(60, days_ago=300), paid(70, days_ago=280), paid(50, days_ago=260)]
    recent = [paid(0, days_ago=30), paid(0, days_ago=20), paid(0, days_ago=10)]
    result = score_of(old + recent)
    assert result["trend"]["direction"] == "improving"


def test_trend_is_steady_when_nothing_much_changed() -> None:
    history = [paid(10, days_ago=n) for n in (300, 280, 260, 30, 20, 10)]
    assert score_of(history)["trend"]["direction"] == "steady"


# --- the whole record -----------------------------------------------------

def test_score_record_is_self_describing() -> None:
    result = score_of([paid(12), paid(8, promise_broken=True)])
    assert set(result) == {
        "buyer_id", "name", "score", "confidence", "history_count",
        "signals", "breakdown", "trend", "as_of",
    }
    assert result["as_of"] == TODAY.isoformat()
    assert 0 <= result["score"] <= 100


def test_explain_mentions_every_factor() -> None:
    result = score_of([paid(12), paid(8, promise_broken=True)])
    text = score.explain(result)
    for item in result["breakdown"]:
        assert item["factor"] in text


def test_scores_are_ranked_worst_first() -> None:
    good = {"buyer_id": "BUY-GOOD", "name": "Good"}
    bad = {"buyer_id": "BUY-BAD", "name": "Bad"}
    grouped = {"BUY-GOOD": [paid(0), paid(0)], "BUY-BAD": [paid(90), paid(90)]}
    ranked = score.score_all([good, bad], grouped, TODAY)
    assert [r["buyer_id"] for r in ranked] == ["BUY-BAD", "BUY-GOOD"]
