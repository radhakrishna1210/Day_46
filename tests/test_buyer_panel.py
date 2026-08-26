"""W2 -- tests for engine/buyer_panel.py's buyer-level rollup.

Mirrors tests/test_early_warning_wiring.py's style: fixtures constructed
directly, not inferred from one seed's own data, so every claim here holds
regardless of what data/generate.py happens to produce for seed 42. Only the
public buyer_panel() entry point is exercised -- see tests/test_watchdog.py
for the same convention against engine/watchdog.py's private helpers.
"""

from __future__ import annotations

from datetime import date, timedelta

from engine import buyer_panel as bp
from report import build_report

TODAY = date(2026, 8, 24)
OVERDUE_ACCEPTANCE = (TODAY - timedelta(days=100)).isoformat()   # ~55 days overdue
NOT_YET_DUE_ACCEPTANCE = (TODAY - timedelta(days=5)).isoformat()  # due in ~40 days


def invoice(
    *, invoice_id: str, buyer_id: str, status: str = "open", amount: int = 1_00_000_00,
    acceptance: str = OVERDUE_ACCEPTANCE, agreed_days: int = 45, paid_date: str | None = None,
) -> dict:
    return {
        "invoice_id": invoice_id, "buyer_id": buyer_id,
        "acceptance_date": acceptance, "written_agreement": True,
        "agreed_days": agreed_days, "agreed_due_date": None,
        "status": status, "amount_paise": amount, "amount_paid_paise": 0,
        "paid_date": paid_date,
    }


def promise(*, invoice_id: str, buyer_id: str, status: str, promised_date: str) -> dict:
    return {
        "promise_id": f"PRM-{invoice_id}-{promised_date}", "invoice_id": invoice_id,
        "buyer_id": buyer_id, "promised_date": promised_date, "amount": "full",
        "status": status, "quote": "test", "recorded_on": TODAY.isoformat(), "broken_on": None,
    }


def _score(score: int = 60, confidence: str = "medium", direction: str = "steady") -> dict:
    return {"score": score, "confidence": confidence, "trend": {"direction": direction}}


def _grouped(*invoices: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for inv in invoices:
        grouped.setdefault(inv["buyer_id"], []).append(inv)
    return grouped


def _promises_by_invoice(*promises: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for p in promises:
        grouped.setdefault(p["invoice_id"], []).append(p)
    return grouped


def _buyers(*ids: str) -> list[dict]:
    return [{"buyer_id": bid, "name": f"Buyer {bid}"} for bid in ids]


# --------------------------------------------------------------------------
# promise reliability: kept/broken/in-flight, and the resolved-late-only rule
# --------------------------------------------------------------------------

def test_promise_reliability_hand_computed_example() -> None:
    """One buyer, five promises across four invoices -- hand-computed:

    kept=1, broken=3, in_flight=1 -> settled=4, reliability = 1/4 = 25.0%.
    Of the three broken promises, only the two whose invoice went on to be
    PAID contribute to avg_days_late (10 and 14 days -> average 12.0); the
    third is broken on an invoice still open, so there is nothing to average
    for it yet.
    """
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")           # keeps the buyer in the panel
    paid_a = invoice(invoice_id="INV-2", buyer_id="BUY-1", status="paid", paid_date="2026-06-20")
    paid_b = invoice(invoice_id="INV-3", buyer_id="BUY-1", status="paid", paid_date="2026-05-15")
    unresolved = invoice(invoice_id="INV-4", buyer_id="BUY-1")

    promises = _promises_by_invoice(
        promise(invoice_id="INV-1", buyer_id="BUY-1", status="open", promised_date="2026-08-29"),
        promise(invoice_id="INV-1", buyer_id="BUY-1", status="kept", promised_date="2026-07-01"),
        promise(invoice_id="INV-2", buyer_id="BUY-1", status="broken", promised_date="2026-06-10"),
        promise(invoice_id="INV-3", buyer_id="BUY-1", status="broken", promised_date="2026-05-01"),
        promise(invoice_id="INV-4", buyer_id="BUY-1", status="broken", promised_date="2026-08-01"),
    )

    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding, paid_a, paid_b, unresolved), promises,
        {}, {"BUY-1": _score()}, {}, TODAY,
    )

    assert len(rows) == 1
    p = rows[0]["promises"]
    assert (p["made"], p["kept"], p["broken"], p["in_flight"]) == (5, 1, 3, 1)
    assert p["reliability_pct"] == 25.0
    assert p["avg_days_late"] == 12.0     # (10 + 14) / 2


def test_zero_promises_is_none_not_zero_percent() -> None:
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding), {}, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    p = rows[0]["promises"]
    assert (p["made"], p["reliability_pct"], p["avg_days_late"]) == (0, None, None)


def test_broken_promises_with_no_resolving_payment_give_no_average() -> None:
    """Broken promises exist, but none of them sit on an invoice that was
    ever paid -- avg_days_late must be None, not a misleading number, even
    though reliability_pct (which only needs kept+broken, not resolution) is
    perfectly well-defined here.
    """
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")
    promises = _promises_by_invoice(
        promise(invoice_id="INV-1", buyer_id="BUY-1", status="broken", promised_date="2026-08-01"),
    )
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding), promises, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    p = rows[0]["promises"]
    assert (p["broken"], p["reliability_pct"], p["avg_days_late"]) == (1, 0.0, None)


# --------------------------------------------------------------------------
# response rate
# --------------------------------------------------------------------------

def test_response_rate_counts_every_non_silent_outcome() -> None:
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")
    history = {
        "INV-1": [
            {"date": "2026-07-01", "rung": 1, "outcome": "no_reply"},
            {"date": "2026-07-08", "rung": 1, "outcome": "promise_made"},
            {"date": "2026-07-20", "rung": 2, "outcome": "paid_full"},
        ],
    }
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding), {}, history, {"BUY-1": _score()}, {}, TODAY,
    )
    r = rows[0]["response"]
    assert (r["messages_sent"], r["replies"], r["response_rate_pct"]) == (3, 2, 66.7)


def test_response_rate_none_when_never_contacted() -> None:
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding), {}, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    r = rows[0]["response"]
    assert (r["messages_sent"], r["response_rate_pct"]) == (0, None)


# --------------------------------------------------------------------------
# recovery state buckets
# --------------------------------------------------------------------------

def test_recovery_state_buckets_by_last_action_kind() -> None:
    invs = [
        invoice(invoice_id="INV-WAIT", buyer_id="BUY-1"),
        invoice(invoice_id="INV-SEND", buyer_id="BUY-1"),
        invoice(invoice_id="INV-HANDOFF", buyer_id="BUY-1"),
        invoice(invoice_id="INV-STOP", buyer_id="BUY-1"),
        invoice(invoice_id="INV-UNSEEN", buyer_id="BUY-1", acceptance=NOT_YET_DUE_ACCEPTANCE),
    ]
    last_action = {
        "INV-WAIT": {"kind": "wait", "rung": 1, "reason": "x"},
        "INV-SEND": {"kind": "send", "rung": 1, "reason": "x"},
        "INV-HANDOFF": {"kind": "handoff", "rung": 4, "reason": "x"},
        "INV-STOP": {"kind": "stop", "rung": 0, "reason": "opted out"},
        # INV-UNSEEN deliberately has no entry -- never yet reached by the brain.
    }
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(*invs), {}, {}, {"BUY-1": _score()}, last_action, TODAY,
    )
    state = rows[0]["recovery_state"]
    assert state == {"not_yet_due": 1, "in_ladder": 2, "handed_off": 1, "stopped": 1}


# --------------------------------------------------------------------------
# which buyers appear, and in what order
# --------------------------------------------------------------------------

def test_buyer_with_everything_paid_does_not_appear() -> None:
    all_paid = invoice(invoice_id="INV-1", buyer_id="BUY-1", status="paid", paid_date="2026-08-01")
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(all_paid), {}, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    assert rows == []


def test_empty_panel_when_no_buyer_has_any_invoice_at_all() -> None:
    rows = bp.buyer_panel(_buyers("BUY-1", "BUY-2"), {}, {}, {}, {}, {}, TODAY)
    assert rows == []


def test_sorted_worst_first_by_total_outstanding() -> None:
    small = invoice(invoice_id="INV-SMALL", buyer_id="BUY-SMALL", amount=10_000_00)
    big = invoice(invoice_id="INV-BIG", buyer_id="BUY-BIG", amount=90_000_00)
    scores = {"BUY-SMALL": _score(), "BUY-BIG": _score()}
    rows = bp.buyer_panel(
        _buyers("BUY-SMALL", "BUY-BIG"), _grouped(small, big), {}, {}, scores, {}, TODAY,
    )
    assert [r["buyer_id"] for r in rows] == ["BUY-BIG", "BUY-SMALL"]


def test_invalid_invoice_excluded_but_buyer_still_appears_via_a_valid_one() -> None:
    valid = invoice(invoice_id="INV-VALID", buyer_id="BUY-1", amount=50_000_00)
    invalid = invoice(invoice_id="INV-INVALID", buyer_id="BUY-1", amount=999_999_00)
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(valid, invalid), {}, {}, {"BUY-1": _score()}, {}, TODAY,
        invalid_ids=frozenset({"INV-INVALID"}),
    )
    assert len(rows) == 1
    assert rows[0]["outstanding_paise"] == 50_000_00     # the invalid invoice's amount never counted
    assert rows[0]["overdue_count"] == 1                  # only the valid invoice


def test_buyer_whose_only_outstanding_invoice_is_invalid_does_not_appear() -> None:
    invalid = invoice(invoice_id="INV-INVALID", buyer_id="BUY-1")
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(invalid), {}, {}, {"BUY-1": _score()}, {}, TODAY,
        invalid_ids=frozenset({"INV-INVALID"}),
    )
    assert rows == []


# --------------------------------------------------------------------------
# score/confidence/trend is a pure pass-through, and overdue math
# --------------------------------------------------------------------------

def test_score_confidence_trend_are_reused_unmodified() -> None:
    outstanding = invoice(invoice_id="INV-1", buyer_id="BUY-1")
    score = _score(score=42, confidence="low", direction="worsening")
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(outstanding), {}, {}, {"BUY-1": score}, {}, TODAY,
    )
    assert rows[0]["score"] is score   # the exact object score.py produced, not a recomputation


def test_oldest_days_overdue_is_none_when_nothing_is_overdue_yet() -> None:
    not_due = invoice(invoice_id="INV-1", buyer_id="BUY-1", acceptance=NOT_YET_DUE_ACCEPTANCE)
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(not_due), {}, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    assert (rows[0]["overdue_count"], rows[0]["oldest_days_overdue"]) == (0, None)


def test_oldest_days_overdue_is_the_max_across_overdue_invoices() -> None:
    older = invoice(invoice_id="INV-OLD", buyer_id="BUY-1",
                    acceptance=(TODAY - timedelta(days=150)).isoformat())
    newer = invoice(invoice_id="INV-NEW", buyer_id="BUY-1",
                    acceptance=(TODAY - timedelta(days=100)).isoformat())
    rows = bp.buyer_panel(
        _buyers("BUY-1"), _grouped(older, newer), {}, {}, {"BUY-1": _score()}, {}, TODAY,
    )
    assert rows[0]["overdue_count"] == 2
    assert rows[0]["oldest_days_overdue"] == 105   # (TODAY - (TODAY-150d+45d)).days


# --------------------------------------------------------------------------
# report/build_report.py formatting: the honest-empty-state strings
# --------------------------------------------------------------------------

def _results_with_panel(panel_row: dict) -> dict:
    return {"agent": {"buyer_panel": [panel_row]}}


def test_report_shows_no_promise_history_not_a_bare_zero_percent() -> None:
    row = {
        "buyer_id": "BUY-1", "name": "Buyer 1", "outstanding_paise": 10_000_00,
        "overdue_count": 1, "oldest_days_overdue": 5, "score": _score(),
        "promises": {"made": 0, "kept": 0, "broken": 0, "in_flight": 0,
                    "reliability_pct": None, "avg_days_late": None},
        "response": {"messages_sent": 0, "replies": 0, "response_rate_pct": None},
        "recovery_state": {"in_ladder": 1, "handed_off": 0, "stopped": 0, "not_yet_due": 0},
    }
    formatted = build_report._buyer_panel_rows(_results_with_panel(row))[0]
    assert formatted["promise_reliability"] == "no promise history"
    assert formatted["avg_days_late"] is None
    assert formatted["response_rate"] == "not yet contacted"


def test_report_distinguishes_unresolved_broken_from_no_history() -> None:
    row = {
        "buyer_id": "BUY-1", "name": "Buyer 1", "outstanding_paise": 10_000_00,
        "overdue_count": 1, "oldest_days_overdue": 5, "score": _score(),
        "promises": {"made": 1, "kept": 0, "broken": 1, "in_flight": 0,
                    "reliability_pct": 0.0, "avg_days_late": None},
        "response": {"messages_sent": 1, "replies": 0, "response_rate_pct": 0.0},
        "recovery_state": {"in_ladder": 1, "handed_off": 0, "stopped": 0, "not_yet_due": 0},
    }
    formatted = build_report._buyer_panel_rows(_results_with_panel(row))[0]
    assert formatted["promise_reliability"] == "0.0%"
    assert formatted["avg_days_late"] == "no resolved-late data"


def test_report_buyer_panel_rows_empty_when_results_has_no_panel_key() -> None:
    assert build_report._buyer_panel_rows({"agent": {}}) == []
