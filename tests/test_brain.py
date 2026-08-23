"""Tests for the brain.

Safety-critical: this module decides whether a real person gets chased for
money today. The tests that matter most are the ones that prove it will NOT
act -- stop rules, the legal ceiling, and the limits on what the LLM may do.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from engine import audit, brain, law, rungs

TODAY = date(2026, 8, 24)          # a Monday


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    """Point the audit log at a temp file so tests never touch the real one."""
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


def invoice(
    *,
    acceptance: str = "2026-06-20",
    written: bool = False,
    agreed_days: int | None = None,
    amount: int = 50_000_000,
    status: str = "open",
    payments: list[dict] | None = None,
) -> dict:
    payments = payments or []
    return {
        "invoice_id": "INV-2026-0204",
        "buyer_id": "BUY-01",
        "description": "400 kg HDPE granules",
        "po_number": "PO/25-26/04821",
        "issue_date": acceptance,
        "acceptance_date": acceptance,
        "written_agreement": written,
        "agreed_days": agreed_days,
        "agreed_due_date": None,
        "amount_paise": amount,
        "status": status,
        "partial_payments": payments,
        "amount_paid_paise": sum(p["amount_paise"] for p in payments),
        "paid_date": None,
    }


def buyer(**overrides) -> dict:
    return {
        "buyer_id": "BUY-01", "name": "ABC Traders", "profile": "corporate",
        "language_pref": "english", "contact_name": "R. Kumar",
        "opted_out": False, **overrides,
    }


def score(value: int) -> dict:
    return {"buyer_id": "BUY-01", "name": "ABC Traders", "score": value,
            "confidence": "high", "history_count": 12}


def contact(day: str, rung: int, outcome: str = "sent", **extra) -> dict:
    return {"date": day, "rung": rung, "channel": "email", "outcome": outcome, **extra}


def run(
    *,
    score_value: int = 62,
    record: dict | None = None,
    who: dict | None = None,
    promises: list[dict] | None = None,
    history: list[dict] | None = None,
    today: date = TODAY,
) -> brain.Action:
    record = record if record is not None else invoice()
    position = law.legal_position(record, today)
    return brain.decide(record, who or buyer(), score(score_value), position,
                        promises or [], history or [])


# --- starting rung by score ----------------------------------------------

def test_a_good_score_starts_at_rung_one() -> None:
    """Score 85: give them the benefit of the doubt."""
    action = run(score_value=85)
    assert action.kind == brain.SEND
    assert action.rung == 1


def test_a_poor_score_starts_at_rung_two() -> None:
    """Score 45: a habitual delayer does not need the soft opening."""
    action = run(score_value=45)
    assert action.kind == brain.SEND
    assert action.rung == 2


def test_a_middling_score_starts_at_rung_one() -> None:
    assert run(score_value=62).rung == 1


def test_the_band_boundaries_are_read_from_config() -> None:
    from engine.config import rules
    config = rules()
    assert brain.band(80, config) == "good"
    assert brain.band(79, config) == "medium"
    assert brain.band(50, config) == "medium"
    assert brain.band(49, config) == "poor"


# --- hard stops -----------------------------------------------------------

def test_the_sixth_contact_is_refused_whatever_the_score() -> None:
    """max_total is 5. Nothing overrides it -- not score, not rung, not age."""
    history = [contact(f"2026-0{month}-01", 2) for month in (3, 4, 5, 6, 7)]
    for value in (95, 62, 20, 0):
        action = run(score_value=value, history=history)
        assert action.kind != brain.SEND, f"score {value} sent a sixth message"
        assert action.kind == brain.HANDOFF


def test_a_fourth_message_at_one_rung_is_refused() -> None:
    """max_messages at rung 2 is 3. The fourth escalates or waits, never sends at 2."""
    history = [contact("2026-07-01", 2), contact("2026-07-10", 2), contact("2026-07-20", 2)]
    action = run(score_value=45, history=history)
    assert not (action.kind == brain.SEND and action.rung == 2)


def test_opt_out_outranks_everything() -> None:
    """Even a long-overdue, low-score, rung-4-eligible case."""
    old = invoice(acceptance="2025-06-01")
    action = run(score_value=10, record=old, who=buyer(opted_out=True))
    assert action.kind == brain.STOP
    assert "opted out" in action.reason


def test_a_dispute_goes_straight_to_a_human() -> None:
    action = run(record=invoice(status="disputed"))
    assert action.kind == brain.HANDOFF
    assert "disputed" in action.reason


def test_a_settled_invoice_stops() -> None:
    paid = invoice(payments=[{"date": "2026-08-01", "amount_paise": 50_000_000}],
                   status="paid")
    assert run(record=paid).kind == brain.STOP


def test_an_invoice_not_yet_due_waits() -> None:
    action = run(record=invoice(acceptance="2026-08-20"))
    assert action.kind == brain.WAIT
    assert action.rung == 0


def test_nothing_is_sent_at_the_weekend() -> None:
    saturday = date(2026, 8, 29)
    action = run(today=saturday)
    assert action.kind == brain.WAIT
    assert "weekend" in action.reason
    assert action.next_review_date.weekday() == 0


def test_messages_are_spaced_apart() -> None:
    action = run(score_value=45, history=[contact("2026-08-23", 2)])
    assert action.kind == brain.WAIT
    assert "days between messages" in action.reason


# --- promises -------------------------------------------------------------

def test_an_active_promise_buys_silence() -> None:
    """Chasing someone who just promised is rude and costs the relationship."""
    action = run(promises=[{"invoice_id": "INV-2026-0204",
                            "promised_date": "2026-09-05", "status": "open"}])
    assert action.kind == brain.WAIT
    assert action.rung == 0
    assert "promised" in action.reason


def test_a_broken_promise_moves_the_case_up_a_rung() -> None:
    broken = [{"invoice_id": "INV-2026-0204", "promised_date": "2026-08-01",
               "status": "open"}]
    without = run(score_value=62, history=[contact("2026-08-01", 1)])
    with_broken = run(score_value=62, history=[contact("2026-08-01", 1)], promises=broken)
    assert with_broken.rung > without.rung


# --- the legal ceiling ----------------------------------------------------

def test_a_broken_promise_cannot_punch_through_the_ceiling() -> None:
    """The +1 is computed inside `desired`, before the cap. It cannot escape it."""
    fresh = invoice(acceptance="2026-07-25")          # overdue, but only just
    position = law.legal_position(fresh, TODAY)
    assert position["available_rung"] == 2, "fixture no longer exercises the cap"

    broken = [{"invoice_id": "INV-2026-0204", "promised_date": "2026-08-01",
               "status": "open"}]
    action = brain.decide(fresh, buyer(), score(45), position, broken,
                          [contact("2026-08-05", 2)])
    assert action.rung <= position["available_rung"]
    assert action.escalation_capped is True


def test_the_invariant_holds_across_the_whole_seeded_queue() -> None:
    """chosen == 0 OR 1 <= chosen <= available_rung, swept over real data."""
    from data import store
    from engine import watchdog

    buyers = {b["buyer_id"]: b for b in store.load_buyers()}
    invoices = store.load_invoices()
    checked = 0
    for offset in (0, 30, 90, 200):
        when = TODAY + timedelta(days=offset)
        for record in watchdog.overdue_invoices(invoices, when)[:25]:
            position = law.legal_position(record, when)
            action = brain.decide(record, buyers[record["buyer_id"]], score(55),
                                  position, [], [], log=False)
            assert action.rung == 0 or 1 <= action.rung <= position["available_rung"], (
                f"{record['invoice_id']} at {when}: rung {action.rung} "
                f"exceeds ceiling {position['available_rung']}"
            )
            checked += 1
    assert checked > 50, "the sweep did not cover enough cases to mean anything"


def test_a_capped_case_with_no_room_waits_rather_than_handing_off() -> None:
    """The ceiling rises with time, so this is a pause, not a terminal state."""
    fresh = invoice(acceptance="2026-07-25")
    position = law.legal_position(fresh, TODAY)
    history = [contact("2026-07-20", 2), contact("2026-07-28", 2), contact("2026-08-05", 2)]
    action = brain.decide(fresh, buyer(), score(45), position, [], history)
    assert action.kind == brain.WAIT
    assert action.next_review_date > TODAY


# --- rung 4: the ordering bug this test exists to prevent ----------------

#: A case that has already worked up to rung 3 and is due to step up again.
#: One prior contact, so max_total (5) is nowhere near in play -- this isolates
#: the rung-4 path from the stop rules entirely.
RUNG_FOUR_HISTORY = [contact("2026-08-10", 3)]


def test_a_rung_four_case_hands_off_rather_than_waiting() -> None:
    """Rung 4 has max_messages of 0.

    A naive per-rung exhaustion check placed above the rung-4 branch would make
    0 >= 0 true on the very first rung-4 decision and swallow it into a wait --
    the final rung would be unreachable and no draft would ever exist.

    Only ONE contact has been made here, so no stop rule is in play: the case
    is at rung 4 purely by climbing the ladder under a ceiling of 4.
    """
    old = invoice(acceptance="2025-06-01")
    position = law.legal_position(old, TODAY)
    assert position["available_rung"] == 4

    action = brain.decide(old, buyer(), score(45), position, [], RUNG_FOUR_HISTORY)
    assert action.kind == brain.HANDOFF, "rung 4 was swallowed by a send gate"
    assert action.rung == 4
    assert action.detail["samadhaan_draft"] is not None
    assert len(RUNG_FOUR_HISTORY) < 5, "this must not be a max_total handoff in disguise"


def test_the_walk_escalates_past_a_rung_with_no_room_left() -> None:
    """Step 7b: rung 2 is used up, so the case moves to rung 3 rather than waiting."""
    old = invoice(acceptance="2025-06-01")
    position = law.legal_position(old, TODAY)
    history = [contact("2026-08-20", 2), contact("2026-08-21", 2), contact("2026-08-22", 2)]
    action = brain.decide(old, buyer(), score(45), position, [], history)
    assert action.rung == 3
    assert action.rung <= position["available_rung"]


def test_the_walk_cannot_climb_above_the_ceiling() -> None:
    """Rung 2 is exhausted but the ceiling is 2, so there is nowhere to go."""
    fresh = invoice(acceptance="2026-07-25")
    position = law.legal_position(fresh, TODAY)
    assert position["available_rung"] == 2
    history = [contact("2026-08-20", 2), contact("2026-08-21", 2), contact("2026-08-22", 2)]
    action = brain.decide(fresh, buyer(), score(45), position, [], history)
    assert action.rung == 2
    assert action.kind == brain.WAIT


def test_rung_zero_never_reaches_the_per_rung_check() -> None:
    """Rung 0 also has max_messages of 0, and must never be gated by it."""
    action = run(promises=[{"invoice_id": "INV-2026-0204",
                            "promised_date": "2026-09-05", "status": "open"}])
    assert action.rung == 0
    assert action.kind == brain.WAIT
    assert "message" not in action.reason


def test_the_handoff_draft_reports_its_own_readiness() -> None:
    old = invoice(acceptance="2025-06-01")
    action = brain.decide(old, buyer(), score(45), law.legal_position(old, TODAY),
                          [], RUNG_FOUR_HISTORY)
    draft = action.detail["samadhaan_draft"]
    assert draft["ready"] is False               # placeholder Udyam number
    assert draft["blockers"]


# --- the LLM boundary -----------------------------------------------------

def ambiguous_case() -> tuple[dict, dict, list]:
    record = invoice(payments=[{"date": "2026-08-01", "amount_paise": 20_000_000}],
                     status="partially_paid")
    position = law.legal_position(record, TODAY)
    history = [contact("2026-08-01", 2, outcome="partial_payment"),
               contact("2026-08-10", 2, outcome="unclear_reply",
                       reply="boss thoda dekhte hain, kuch adjust karna padega")]
    return record, position, history


def test_an_ambiguous_case_is_routed_to_the_model() -> None:
    """Partial payment plus a reply we could not classify."""
    record, position, history = ambiguous_case()
    action = brain.decide(record, buyer(), score(55), position, [], history)
    assert action.source == "llm"


def test_the_ambiguous_decision_is_logged_with_source_llm() -> None:
    record, position, history = ambiguous_case()
    brain.decide(record, buyer(), score(55), position, [], history)
    entries = audit.entries_for("INV-2026-0204")
    assert entries and entries[-1]["source"] == "llm"


def test_the_model_may_soften_but_never_escalate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model demanding escalation is ignored, and the rules stand."""
    record, position, history = ambiguous_case()
    monkeypatch.setattr(
        brain, "llm",
        lambda prompt, purpose: '{"decision": "escalate", "reason": "push harder"}',
    )
    action = brain.decide(record, buyer(), score(55), position, [], history)
    assert action.kind != brain.WAIT or action.detail.get("llm_decision") == "wait"
    assert action.rung <= position["available_rung"]
    assert action.detail.get("llm_ignored") is True


def test_an_unambiguous_case_never_calls_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(prompt, purpose):
        raise AssertionError("the model was consulted on a case the rules can settle")
    monkeypatch.setattr(brain, "llm", explode)
    assert run(score_value=62).source == "rule"


# --- the audit trail ------------------------------------------------------

def test_every_decision_is_logged() -> None:
    run(score_value=85)
    entries = audit.entries()
    assert len(entries) == 1
    assert entries[0]["actor"] == "brain"


def test_audit_lines_are_valid_json_with_every_field() -> None:
    run(score_value=85)
    raw = audit.LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    for line in raw:
        entry = json.loads(line)
        for key in ("ts", "invoice_id", "buyer_id", "actor", "action", "reason", "source"):
            assert key in entry, f"missing {key}"
        assert entry["source"] in {"rule", "llm"}


def test_the_timestamp_comes_from_the_simulation_clock_not_the_wall() -> None:
    """A log that differs between two identical runs cannot be trusted."""
    run(score_value=85, today=date(2026, 3, 3))
    assert audit.entries()[0]["ts"].startswith("2026-03-03")


def test_a_dry_run_decides_but_writes_nothing() -> None:
    record = invoice()
    brain.decide(record, buyer(), score(85), law.legal_position(record, TODAY),
                 [], [], log=False)
    assert audit.entries() == []


# --- every action is explainable -----------------------------------------

@pytest.mark.parametrize("case", [
    {"score_value": 85},
    {"score_value": 45},
    {"who": buyer(opted_out=True)},
    {"record": invoice(status="disputed")},
    {"record": invoice(acceptance="2025-06-01")},
    {"promises": [{"invoice_id": "INV-2026-0204", "promised_date": "2026-09-05",
                   "status": "open"}]},
])
def test_every_action_carries_a_reason(case: dict) -> None:
    action = run(**case)
    assert action.reason and len(action.reason) > 10
    assert action.kind in {brain.WAIT, brain.SEND, brain.HANDOFF, brain.STOP}


def test_a_send_carries_the_skeleton_the_writer_must_follow() -> None:
    action = run(score_value=45)
    assert action.kind == brain.SEND
    assert action.skeleton is not None
    assert action.skeleton["rung"] == action.rung
    assert action.skeleton["forbidden"]


def test_a_non_send_carries_no_skeleton() -> None:
    assert run(who=buyer(opted_out=True)).skeleton is None
