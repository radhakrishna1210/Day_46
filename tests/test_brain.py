"""Tests for the brain.

Safety-critical: this module decides whether a real person gets chased for
money today. The tests that matter most are the ones that prove it will NOT
act -- stop rules, the legal ceiling, and the limits on what the LLM may do.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from engine import ability_willingness as aw
from engine import audit, brain, law, negotiation, rungs

TODAY = date(2026, 8, 24)          # a Monday


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    """Point the audit log at a temp file so tests never touch the real one."""
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


def invoice(
    *,
    acceptance: str = "2026-08-06",   # 3 days overdue as of TODAY -- barely, on purpose
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


# --- P2: a first contact is paced for the backlog it inherited ------------

def test_a_backlogged_first_contact_opens_above_the_base_rung() -> None:
    """A good-score buyer already 10 days overdue does not get the same soft

    opening as one that just became overdue -- the ladder's own cadence
    (7 days between rungs, for a good score) says one step should already
    have happened.
    """
    old_enough = invoice(acceptance="2026-07-30")     # 10 days overdue at TODAY
    action = run(score_value=85, record=old_enough)
    assert action.kind == brain.SEND
    assert action.rung == 2
    assert "paced one rung ahead" in action.reason


def test_a_fresh_first_contact_is_not_treated_as_backlog() -> None:
    """The barely-overdue default case keeps opening at the plain base rung."""
    action = run(score_value=85)
    assert action.rung == 1
    assert "paced" not in action.reason


def test_the_backlog_bump_is_still_capped_by_the_ceiling() -> None:
    """The one-step backlog bump never outruns what the law supports.

    A poor-score buyer only 10 days overdue would want rung 3 (base 2 + the
    backlog step), but the law has not caught up to that yet.
    """
    old_enough = invoice(acceptance="2026-07-30")     # 10 days overdue at TODAY
    position = law.legal_position(old_enough, TODAY)
    action = run(score_value=45, record=old_enough)
    assert position["available_rung"] == 2
    assert action.kind == brain.SEND
    assert action.rung == 2


def test_a_severely_backlogged_first_contact_still_gets_one_message_before_handoff() -> None:
    """An invoice inherited hundreds of days overdue does not open on a stop

    with the buyer never once contacted -- it gets one real message (one
    rung above base), and ordinary escalation carries it the rest of the way
    on later days.
    """
    ancient = invoice(acceptance="2025-01-01")
    position = law.legal_position(ancient, TODAY)
    action = run(score_value=45, record=ancient)
    assert position["available_rung"] == 4, "fixture no longer exercises a maxed-out ceiling"
    assert action.kind == brain.SEND
    assert action.rung == 3


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
    """docs/edge_cases.md TC-075/TC-140's general rule. Even a long-overdue,
    low-score, rung-4-eligible case.
    """
    old = invoice(acceptance="2025-06-01")
    action = run(score_value=10, record=old, who=buyer(opted_out=True))
    assert action.kind == brain.STOP
    assert "opted out" in action.reason


def test_tc140_opt_out_mid_sequence_at_rung_two_stops_everything() -> None:
    """docs/edge_cases.md TC-140, specifically: unlike test_opt_out_outranks_
    everything's long-overdue, rung-4-eligible case above, this builds an
    actual in-progress rung-2 sequence -- two prior contacts, still under the
    3-per-rung cap -- so the case is genuinely active, mid-ladder, when the
    buyer opts out.
    """
    history = [contact("2026-08-01", 2), contact("2026-08-10", 2)]
    still_active = run(score_value=45, history=history, who=buyer(opted_out=False))
    assert still_active.kind == brain.SEND  # the sequence really was still live

    action = run(score_value=45, history=history, who=buyer(opted_out=True))
    assert action.kind == brain.STOP
    assert "opted out" in action.reason


def test_a_dispute_goes_straight_to_a_human() -> None:
    """docs/edge_cases.md TC-027, first half: the immediate handoff."""
    action = run(record=invoice(status="disputed"))
    assert action.kind == brain.HANDOFF
    assert "disputed" in action.reason


def test_a_dispute_never_resumes_sending_on_a_later_pass() -> None:
    """docs/edge_cases.md TC-027, second half: "chasing stops" has to mean
    more than one handoff on one call -- an automated day-loop calls decide()
    again tomorrow, and it must not flip back to SEND for the same disputed
    invoice.
    """
    record = invoice(status="disputed")
    first = run(record=record, today=TODAY)
    second = run(record=record, today=TODAY + timedelta(days=1))
    assert first.kind == brain.HANDOFF
    assert second.kind == brain.HANDOFF
    assert "disputed" in second.reason


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
    # Old enough overdue for a ceiling above rung 2 -- otherwise both cases
    # are capped to the same rung and the jump has nowhere to show up.
    record = invoice(acceptance="2026-06-20")
    broken = [{"invoice_id": "INV-2026-0204", "promised_date": "2026-08-01",
               "status": "open"}]
    without = run(score_value=62, record=record, history=[contact("2026-08-01", 1)])
    with_broken = run(score_value=62, record=record, history=[contact("2026-08-01", 1)],
                      promises=broken)
    assert with_broken.rung > without.rung


# --- TC-014: a buyer who renegotiates before their promise falls due -----
# apply_reply() never cancels a prior open promise (engine/promises.py) -- it
# only ever appends. Confirmed bug this closes: without _not_superseded(), a
# proactively renegotiated promise still counted as its OWN separately
# broken promise once its grace passed, inflating the rung-jump and pushing
# a good-faith renegotiation to a premature human handoff.

def test_tc014_active_promise_returns_the_renegotiated_one_not_the_stale_one() -> None:
    """Both are simultaneously "open" -- see docs/edge_cases.md TC-014's own
    scenario -- so active_promise() must not return the one appended first.
    """
    superseded = {"invoice_id": "INV-2026-0204", "promised_date": "2026-09-05",
                 "status": "open", "recorded_on": "2026-08-01"}
    current = {"invoice_id": "INV-2026-0204", "promised_date": "2026-09-20",
              "status": "open", "recorded_on": "2026-08-03"}
    active = brain.active_promise([superseded, current], date(2026, 8, 10), grace_days=3)
    assert active is current


def test_tc014_a_superseded_promise_is_never_counted_as_broken() -> None:
    superseded = {"invoice_id": "INV-2026-0204", "promised_date": "2026-07-01",
                 "status": "broken", "recorded_on": "2026-06-25"}
    current = {"invoice_id": "INV-2026-0204", "promised_date": "2026-07-15",
              "status": "broken", "recorded_on": "2026-06-28"}
    assert brain.broken_promises([superseded, current], TODAY, grace_days=3) == 1


def test_tc014_a_renegotiated_promise_does_not_double_escalate_the_case() -> None:
    """The bug, end to end: a buyer who renegotiated once in good faith must
    not be escalated as if they had broken two independent promises.
    """
    record = invoice(acceptance="2026-06-20")
    only_current = [{"invoice_id": "INV-2026-0204", "promised_date": "2026-07-15",
                     "status": "broken", "recorded_on": "2026-06-28"}]
    superseded_plus_current = [
        {"invoice_id": "INV-2026-0204", "promised_date": "2026-07-01",
         "status": "broken", "recorded_on": "2026-06-25"},
        {"invoice_id": "INV-2026-0204", "promised_date": "2026-07-15",
         "status": "broken", "recorded_on": "2026-06-28"},
    ]
    history = [contact("2026-08-01", 1)]
    without_stale = run(score_value=62, record=record, history=history, promises=only_current)
    with_stale = run(score_value=62, record=record, history=history,
                     promises=superseded_plus_current)
    assert with_stale.rung == without_stale.rung
    assert with_stale.kind == without_stale.kind


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
    """Part of the money in, and a reply nobody could classify.

    The partial payment is established ONLY by the invoice itself -- there is
    deliberately no "partial_payment" marker in the contact history, so this
    fixture cannot pass by way of a history flag. It exercises the comparison
    of outstanding principal against the invoice amount and nothing else.
    """
    record = invoice(payments=[{"date": "2026-08-01", "amount_paise": 20_000_000}],
                     status="partially_paid")
    position = law.legal_position(record, TODAY)
    assert position["principal_paise"] < record["amount_paise"], "fixture is not part-paid"
    history = [contact("2026-08-01", 2),
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


# --- confidence modifies pacing ------------------------------------------
# A score built on almost no history is not evidence. Low confidence clamps the
# buyer to the middle band in BOTH directions: two late invoices is no more
# proof of a habitual delayer than two prompt ones is proof of a good payer.

def thin(value: int) -> dict:
    """A score from almost no history -- the low-confidence case."""
    return {"buyer_id": "BUY-01", "name": "ABC Traders", "score": value,
            "confidence": "low", "history_count": 2}


def decide_with(score_record: dict, *, record=None, history=None, today=TODAY):
    record = record if record is not None else invoice()
    return brain.decide(record, buyer(), score_record,
                        law.legal_position(record, today), [], history or [])


def test_a_high_score_on_thin_history_is_paced_as_ordinary() -> None:
    """BUY-07's case: 87 from two invoices should not buy the 7-day patience."""
    action = decide_with(thin(87))
    assert action.detail["scored_band"] == "good"
    assert action.detail["effective_band"] == "medium"


def test_a_low_score_on_thin_history_is_treated_more_gently() -> None:
    """The symmetric half. Two late invoices is not proof of a habitual delayer."""
    thin_action = decide_with(thin(20))
    thick_action = decide_with({**thin(20), "confidence": "high", "history_count": 14})
    assert thin_action.rung == 1, "a barely-known buyer should not open at rung 2"
    assert thick_action.rung == 2, "a well-known poor payer still opens at rung 2"


def test_a_confident_score_is_left_alone() -> None:
    for value, expected in ((87, "good"), (62, "medium"), (20, "poor")):
        action = decide_with({"buyer_id": "BUY-01", "name": "ABC", "score": value,
                              "confidence": "high", "history_count": 14})
        assert action.detail["scored_band"] == expected
        assert action.detail["effective_band"] == expected


def test_the_clamp_changes_how_fast_the_case_escalates() -> None:
    """good waits 7 days between rungs, medium waits 5. Thin history gets 5."""
    history = [contact("2026-08-18", 1)]          # 6 days ago
    thick = decide_with({**thin(87), "confidence": "high", "history_count": 14},
                        history=history)
    lean = decide_with(thin(87), history=history)
    assert lean.rung > thick.rung, "the clamp did not speed up escalation"


def test_the_clamp_is_config_driven(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting low_confidence_band to null paces purely on score."""
    from engine.config import rules
    config = rules()
    disabled = {**config, "ladder": {**config["ladder"], "low_confidence_band": None}}
    record = invoice()
    action = brain.decide(record, buyer(), thin(20), law.legal_position(record, TODAY),
                          [], [], config=disabled)
    assert action.detail["effective_band"] == "poor"
    assert action.rung == 2


def test_the_reason_says_when_confidence_changed_the_pacing() -> None:
    reason = decide_with(thin(87)).reason
    assert "low confidence" in reason
    assert "2 settled invoices" in reason


def test_the_reason_stays_quiet_when_confidence_changed_nothing() -> None:
    reason = decide_with({"buyer_id": "BUY-01", "name": "ABC", "score": 87,
                          "confidence": "high", "history_count": 14}).reason
    assert "low confidence" not in reason


def test_the_clamp_does_not_touch_the_ceiling() -> None:
    """Pacing picks a starting rung; the law still decides the maximum."""
    fresh = invoice(acceptance="2026-07-25")
    position = law.legal_position(fresh, TODAY)
    action = brain.decide(fresh, buyer(), thin(20), position, [], [])
    assert action.rung == 0 or 1 <= action.rung <= position["available_rung"]


def test_an_unclear_reply_alone_is_not_ambiguous() -> None:
    """The negative control.

    Same confusing reply, but nothing has been paid. The rules can settle this
    perfectly well -- an unpaid invoice with a vague answer is just an unpaid
    invoice -- so the model must not be consulted. Without this test, a bug
    that made _is_ambiguous always true would still pass every other test here.
    """
    record = invoice()                      # nothing paid at all
    position = law.legal_position(record, TODAY)
    assert position["principal_paise"] == record["amount_paise"]
    history = [contact("2026-08-10", 2, outcome="unclear_reply",
                       reply="boss thoda dekhte hain")]
    action = brain.decide(record, buyer(), score(55), position, [], history)
    assert action.source == "rule"


def test_a_partial_payment_alone_is_not_ambiguous() -> None:
    """The other half of the control: part-paid, but the reply was clear."""
    record = invoice(payments=[{"date": "2026-08-01", "amount_paise": 20_000_000}],
                     status="partially_paid")
    position = law.legal_position(record, TODAY)
    history = [contact("2026-08-10", 2, outcome="sent")]
    action = brain.decide(record, buyer(), score(55), position, [], history)
    assert action.source == "rule"


def test_ambiguity_is_decided_by_the_invoice_amount_not_a_history_flag() -> None:
    """Pins _is_ambiguous to the comparison it actually makes.

    A history entry claiming a partial payment must NOT make a fully unpaid
    invoice ambiguous. This is the fallback path the old implementation relied
    on, and it is no longer load-bearing.
    """
    record = invoice()                      # fully unpaid
    position = law.legal_position(record, TODAY)
    history = [contact("2026-08-01", 2, outcome="partial_payment"),
               contact("2026-08-10", 2, outcome="unclear_reply", reply="hmm")]
    action = brain.decide(record, buyer(), score(55), position, [], history)
    assert action.source == "rule", "a history flag revived the old fallback path"


# --------------------------------------------------------------------------
# Phase 3 -- EV-informed action selection (config/rules.yaml brain.ev_mode)
# --------------------------------------------------------------------------

def score_with_quadrant(quadrant: str, *, value: int = 70, broken: int = 0) -> dict:
    """A two-axis-shaped score record: score()'s own fields, plus the
    signals.broken_promises and quadrant keys only engine.ability_willingness.
    two_axis_score() output carries and that decide()'s EV branch reads."""
    return {
        "buyer_id": "BUY-01", "name": "ABC Traders", "score": value,
        "confidence": "high", "history_count": 12,
        "signals": {"broken_promises": broken}, "quadrant": quadrant,
    }


def ev_config(**overrides) -> dict:
    """config/rules.yaml with brain.ev_mode forced on, for tests that need it."""
    from engine.config import rules
    config = rules()
    merged = {**config, "brain": {**config["brain"], "ev_mode": "on"}}
    return {**merged, **overrides}


#: Old enough that the legal ceiling is fully open (rung 4). NOT old enough,
#: on its own, to make chosen_rung reach HANDOFF_RUNG -- with history=[]
#: (first contact), decide()'s own backlog formula can never desire more
#: than base + 1, so chosen stays well below 4 regardless of how wide open
#: the ceiling is. This fixture exists specifically to exercise that gap:
#: see test_ev_mode_never_jumps_to_handoff_just_because_the_ceiling_is_open.
OLD_ENOUGH_FOR_CEILING_4 = "2026-06-01"


def _chosen_rung_with_ev_off(record: dict, quadrant: str, **score_kwargs) -> int:
    """The escalation walk's own chosen rung for this fixture, read off a
    plain ev_mode: off decide() call rather than recomputed by hand -- the
    single source of truth for "what would the non-EV path have done" that
    every EV-mode test below compares against."""
    position = law.legal_position(record, TODAY)
    off = brain.decide(record, buyer(), score_with_quadrant(quadrant, **score_kwargs), position,
                       [], [], log=False)
    assert off.kind != brain.HANDOFF, (
        "fixture assumption: this scenario must not already resolve to a handoff "
        "via the non-EV path, or there is nothing left for the EV branch to choose"
    )
    return off.rung


@pytest.mark.parametrize("quadrant", list(aw.QUADRANTS))
def test_ev_mode_picks_the_top_ranked_eligible_action_per_quadrant(quadrant: str) -> None:
    """The chosen action always matches negotiation.rank_actions() over
    exactly the candidates config/rules.yaml's negotiation.eligible_actions
    allows for this quadrant AND this invoice's actual chosen_rung, mapped to
    the kind/rung this phase specifies."""
    record = invoice(acceptance=OLD_ENOUGH_FOR_CEILING_4)
    position = law.legal_position(record, TODAY)
    chosen = _chosen_rung_with_ev_off(record, quadrant)

    config = ev_config()
    action = brain.decide(record, buyer(), score_with_quadrant(quadrant), position,
                          [], [], config=config)

    candidates = brain.eligible_negotiation_actions(quadrant, chosen, config)
    expected = negotiation.rank_actions(
        quadrant, aw.outstanding_paise(record), broken_promises=0, candidates=candidates,
    )[0]
    assert action.detail["negotiation_action"] == expected["action"]
    assert action.detail["ev"] == expected

    winner = expected["action"]
    if winner == negotiation.WAIT:
        assert action.kind == brain.WAIT
    elif winner in (negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION):
        assert (action.kind, action.rung) == (brain.HANDOFF, brain.HANDOFF_RUNG)
    elif winner == negotiation.PAYMENT_PLAN:
        assert action.kind == brain.PAYMENT_PLAN
        assert action.skeleton is not None
    elif winner == negotiation.COUNTER_SETTLE:
        assert action.kind == brain.COUNTER_SETTLE
        assert action.skeleton is not None
    else:
        assert action.kind == brain.SEND


def test_ev_mode_never_offers_a_good_customer_legal_pressure() -> None:
    """The good_customer finding this phase's own brief flagged: with the
    full action space, legal_facts (or legal_escalation) outranked
    soft_nudge even for the best-paying quadrant. eligible_actions withholds
    legal_facts/legal_escalation/counter_settle from good_customer entirely,
    so no candidate offering legal pressure is even ranked -- true at every
    chosen_rung, since good_customer's own config list never contains them."""
    config = ev_config()
    for chosen_rung in (0, 1, 2, 3, brain.HANDOFF_RUNG):
        candidates = brain.eligible_negotiation_actions(aw.GOOD_CUSTOMER, chosen_rung, config)
        assert negotiation.LEGAL_FACTS not in candidates
        assert negotiation.LEGAL_ESCALATION not in candidates
        assert negotiation.COUNTER_SETTLE not in candidates


# --- the handoff-reachability gate: never MORE permissive than the non-EV path ---

def test_eligible_negotiation_actions_only_admits_a_handoff_at_the_handoff_rung() -> None:
    """Direct, isolated proof of eligible_negotiation_actions()'s own gate,
    independent of decide()'s control flow (which, as of Phase 3, never
    actually calls it with chosen_rung == HANDOFF_RUNG -- see the next two
    tests). human_handoff/legal_escalation are only ever candidates once
    chosen_rung has ALREADY reached HANDOFF_RUNG, for every quadrant whose
    config list offers them at all."""
    config = ev_config()
    for quadrant in (aw.CASH_FLOW_PROBLEM, aw.CAN_PAY_BUT_WONT, aw.HIGH_RISK):
        below = brain.eligible_negotiation_actions(quadrant, brain.HANDOFF_RUNG - 1, config)
        at = brain.eligible_negotiation_actions(quadrant, brain.HANDOFF_RUNG, config)
        assert negotiation.HUMAN_HANDOFF not in below and negotiation.LEGAL_ESCALATION not in below
        offered = set(config["negotiation"]["eligible_actions"][quadrant])
        assert (negotiation.HUMAN_HANDOFF in at) == (negotiation.HUMAN_HANDOFF in offered)
        assert (negotiation.LEGAL_ESCALATION in at) == (negotiation.LEGAL_ESCALATION in offered)


def test_ev_mode_falls_back_when_the_legal_ceiling_alone_is_not_yet_open() -> None:
    """The plainest case: high_risk's unrestricted top action is
    legal_escalation, but with today's legal ceiling below HANDOFF_RUNG a
    handoff is not yet reachable by any measure -- the Brain must fall back
    to the next eligible candidate (legal_facts, a plain send)."""
    record = invoice(acceptance="2026-08-05")     # a few days overdue, ceiling < 4
    position = law.legal_position(record, TODAY)
    assert position["available_rung"] < brain.HANDOFF_RUNG, "fixture assumption"

    full_ranking = negotiation.rank_actions(
        aw.HIGH_RISK, aw.outstanding_paise(record), broken_promises=0)
    assert full_ranking[0]["action"] == negotiation.LEGAL_ESCALATION, "fixture assumption"

    action = brain.decide(record, buyer(), score_with_quadrant(aw.HIGH_RISK), position,
                          [], [], config=ev_config())
    assert action.kind != brain.HANDOFF
    assert action.detail["negotiation_action"] != negotiation.LEGAL_ESCALATION
    assert action.detail["negotiation_action"] == negotiation.LEGAL_FACTS
    assert 1 <= action.rung <= position["available_rung"]


def test_ev_mode_never_jumps_to_handoff_just_because_the_ceiling_is_open() -> None:
    """The sharper case a plain ceiling check would miss: the legal ceiling
    IS wide open (available_rung == 4), but this is a first-ever contact, so
    the ordinary escalation walk's own chosen_rung cannot possibly have
    reached HANDOFF_RUNG yet (see decide()'s backlog formula -- a first
    contact desires at most base + 1). high_risk's unrestricted top action is
    legal_escalation; EV must still fall back to legal_facts here, exactly as
    it would with a low ceiling -- the ceiling being open is NOT, on its own,
    sufficient to reach a human handoff any sooner than the non-EV path
    would have."""
    record = invoice(acceptance=OLD_ENOUGH_FOR_CEILING_4)
    position = law.legal_position(record, TODAY)
    assert position["available_rung"] == brain.HANDOFF_RUNG, "fixture assumption: ceiling wide open"
    chosen = _chosen_rung_with_ev_off(record, aw.HIGH_RISK)
    assert chosen < brain.HANDOFF_RUNG, "fixture assumption: a first contact never walks to rung 4"

    action = brain.decide(record, buyer(), score_with_quadrant(aw.HIGH_RISK), position,
                          [], [], config=ev_config())
    assert action.kind != brain.HANDOFF
    assert action.detail["negotiation_action"] not in (
        negotiation.HUMAN_HANDOFF, negotiation.LEGAL_ESCALATION)
    assert action.detail["negotiation_action"] == negotiation.LEGAL_FACTS


@pytest.mark.parametrize("ev_mode", ["off", "on"])
def test_hard_stops_short_circuit_before_any_ev_logic_runs(ev_mode: str) -> None:
    """Every existing hard-stop path fires exactly the same way whether or
    not EV mode is on -- opt-out, dispute, settlement, not-yet-due,
    max-contacts, an active promise, the weekend and message spacing are all
    upstream of the EV branch, unconditionally."""
    config = ev_config() if ev_mode == "on" else None
    two_axis = score_with_quadrant(aw.CAN_PAY_BUT_WONT)

    def decide(record, *, who=None, promises=None, history=None, today=TODAY):
        position = law.legal_position(record, today)
        return brain.decide(record, who or buyer(), two_axis, position,
                            promises or [], history or [], config=config)

    opted_out = decide(invoice(acceptance="2025-06-01"), who=buyer(opted_out=True))
    assert opted_out.kind == brain.STOP and "opted out" in opted_out.reason

    disputed = decide(invoice(status="disputed"))
    assert disputed.kind == brain.HANDOFF and "disputed" in disputed.reason

    settled = decide(invoice(payments=[{"date": "2026-08-01", "amount_paise": 50_000_000}],
                             status="paid"))
    assert settled.kind == brain.STOP

    not_due = decide(invoice(acceptance="2026-08-20"))
    assert not_due.kind == brain.WAIT and not_due.rung == 0

    max_contacts_history = [contact(f"2026-0{month}-01", 2) for month in (3, 4, 5, 6, 7)]
    maxed = decide(invoice(), history=max_contacts_history)
    assert maxed.kind == brain.HANDOFF and "reaching the limit" in maxed.reason

    with_a_promise = decide(invoice(), promises=[{
        "invoice_id": "INV-2026-0204", "promised_date": "2026-09-05", "status": "open",
    }])
    assert with_a_promise.kind == brain.WAIT and with_a_promise.rung == 0

    weekend = decide(invoice(), today=date(2026, 8, 29))
    assert weekend.kind == brain.WAIT and "weekend" in weekend.reason

    spaced = decide(invoice(), history=[contact("2026-08-23", 2)])
    assert spaced.kind == brain.WAIT and "days between messages" in spaced.reason


def test_ev_mode_off_is_inert_regardless_of_what_the_score_carries() -> None:
    """With ev_mode off (the default), decide() must produce the exact same
    Action whether the caller passes a plain engine.score.score_buyer()-shaped
    dict or a two-axis one carrying ability/willingness/quadrant on top of it
    -- the extra keys must be inert. This is the invariant sim/run_sim.py's
    Phase 3 switch to two_axis_score() depends on, and what keeps the seed-42
    demo win reproducible with the shipped default (see
    tests/test_run_sim.py's snapshot-diff test for the sim-level proof)."""
    plain = score(70)
    two_axis = {**plain, "ability": {"score": 80, "signals": {}, "breakdown": []},
               "willingness": {"score": 80, "signals": {}, "breakdown": []},
               "quadrant": aw.GOOD_CUSTOMER}

    scenarios = [
        {"record": invoice(), "history": []},
        {"record": invoice(acceptance=OLD_ENOUGH_FOR_CEILING_4), "history": []},
        {"record": invoice(), "history": [contact("2026-08-10", 2)]},
        {"record": invoice(payments=[{"date": "2026-08-01", "amount_paise": 20_000_000}],
                            status="partially_paid"),
         "history": [contact("2026-08-10", 2, outcome="unclear_reply", reply="hmm")]},
    ]
    for scenario in scenarios:
        position = law.legal_position(scenario["record"], TODAY)
        before = brain.decide(scenario["record"], buyer(), plain, position,
                              [], scenario["history"], log=False)
        after = brain.decide(scenario["record"], buyer(), two_axis, position,
                             [], scenario["history"], log=False)
        assert before == after, f"ev_mode: off diverged for {scenario['record']['invoice_id']}"
