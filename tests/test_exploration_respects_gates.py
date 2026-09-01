"""Exploration must never widen what the rules already allow.

sim/run_sim.py's run_agent(explore=True) replaces the EV branch's argmax with
a uniform sample, so a learning run can see what happens after actions the
current EV grid would never have picked. That is a deliberately blunt
instrument pointed at live decisions about chasing people for money, and it is
only safe because of one property:

    exploration chooses WITHIN an already-gated list. It never produces the
    list, never lifts engine/law.py's available_rung() ceiling, and never runs
    at all until every stop rule in brain.decide() has already cleared.

This file is the standing proof of that property, and the file to run first if
anyone ever moves the sampling upstream of the gates. It has three jobs:

  1. across 200 explored decisions, zero actions outside config/rules.yaml's
     negotiation.eligible_actions were executed, and zero exceeded the law
     ceiling (the two the brief asks for, by name);
  2. exploration is not vacuously safe -- it really does pick differently from
     the argmax, or (1) would prove nothing;
  3. it cannot be reached from main.py, by any config edit or CLI flag.
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from engine import ability_willingness as aw
from engine import audit, brain, law, negotiation
from engine.config import rules

TODAY = date(2026, 8, 24)          # a Monday, so the weekend gate is not in play

#: Five acceptance dates spanning the ladder: barely overdue (a low ceiling)
#: through long overdue (ceiling wide open). Fixed, not random -- the sampling
#: under test is the only thing that should vary between cases.
ACCEPTANCES = ("2026-08-06", "2026-07-20", "2026-07-01", "2026-06-10", "2026-05-01")

#: 4 quadrants x 5 acceptance dates x 10 streams == exactly 200 decisions.
EXPLORE_STREAMS = tuple(range(10))
EXPECTED_DECISIONS = len(aw.QUADRANTS) * len(ACCEPTANCES) * len(EXPLORE_STREAMS)

AMOUNT_PAISE = 50_000_000


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    """Point the audit log at a temp file so these runs never touch the real one."""
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


def invoice(*, acceptance: str, amount: int = AMOUNT_PAISE) -> dict:
    return {
        "invoice_id": "INV-2026-0204",
        "buyer_id": "BUY-01",
        "description": "400 kg HDPE granules",
        "po_number": "PO/25-26/04821",
        "issue_date": acceptance,
        "acceptance_date": acceptance,
        "written_agreement": False,
        "agreed_days": None,
        "agreed_due_date": None,
        "amount_paise": amount,
        "status": "open",
        "partial_payments": [],
        "amount_paid_paise": 0,
        "paid_date": None,
    }


def buyer(**overrides) -> dict:
    return {
        "buyer_id": "BUY-01", "name": "ABC Traders", "profile": "corporate",
        "language_pref": "english", "contact_name": "R. Kumar",
        "opted_out": False, **overrides,
    }


def score_with_quadrant(quadrant: str, *, value: int = 70, broken: int = 0) -> dict:
    """A two-axis-shaped score: the plain score_buyer() fields, plus the
    signals.broken_promises and quadrant keys decide()'s EV branch reads."""
    return {
        "buyer_id": "BUY-01", "name": "ABC Traders", "score": value,
        "confidence": "high", "history_count": 12,
        "signals": {"broken_promises": broken}, "quadrant": quadrant,
    }


def ev_config() -> dict:
    config = rules()
    return {**config, "brain": {**config["brain"], "ev_mode": "on"}}


#: The Action.kind each negotiation action must map to once executed. Spelled
#: out here rather than imported from engine.brain, so a change to that
#: mapping has to be made deliberately in two places instead of silently
#: agreeing with itself.
EXPECTED_KIND = {
    negotiation.WAIT: brain.WAIT,
    negotiation.SOFT_NUDGE: brain.SEND,
    negotiation.FIRM: brain.SEND,
    negotiation.LEGAL_FACTS: brain.SEND,
    negotiation.PAYMENT_PLAN: brain.PAYMENT_PLAN,
    negotiation.COUNTER_SETTLE: brain.COUNTER_SETTLE,
    negotiation.HUMAN_HANDOFF: brain.HANDOFF,
    negotiation.LEGAL_ESCALATION: brain.HANDOFF,
}


def explored_decisions() -> list[dict]:
    """The 200 decisions every assertion below is made against.

    Rebuilt per calling test rather than shared through a fixture: each one is
    cheap, and a mutable list of dicts shared between tests is a worse trade
    than the repeat.

    Each entry carries everything an assertion needs -- the decision, the
    quadrant it was made for, the ceiling the law handed the brain, and the
    decision the ordinary escalation walk reached for the same case with
    exploration switched off, read off a real call rather than recomputed by
    hand.
    """
    config = ev_config()
    out: list[dict] = []
    for quadrant in aw.QUADRANTS:
        for acceptance in ACCEPTANCES:
            record = invoice(acceptance=acceptance)
            position = law.legal_position(record, TODAY)
            who, scored = buyer(), score_with_quadrant(quadrant)
            plain = brain.decide(record, who, scored, position, [], [], log=False)

            for stream in EXPLORE_STREAMS:
                action = brain.decide(
                    record, who, scored, position, [], [], config=config, log=False,
                    explore_rng=random.Random(f"{quadrant}|{acceptance}|{stream}"),
                )
                out.append({
                    "action": action,
                    "quadrant": quadrant,
                    "ceiling": int(position["available_rung"]),
                    "plain": plain,
                })
    return out


def argmax_action(quadrant: str, chosen_rung: int) -> str:
    """What the shipped EV policy would have picked for the same case."""
    config = ev_config()
    candidates = brain.eligible_negotiation_actions(quadrant, chosen_rung, config)
    return negotiation.rank_actions(
        quadrant, AMOUNT_PAISE, broken_promises=0, candidates=candidates,
    )[0]["action"]


# --------------------------------------------------------------------------
# the two the brief asks for, by name
# --------------------------------------------------------------------------

def test_two_hundred_explored_decisions_stay_inside_eligible_actions() -> None:
    """Zero actions outside config/rules.yaml's eligible_actions were executed.

    Checked at three tightening levels, because only the third would catch a
    sampler that had been moved upstream of the gates:

      * the sampled action is in the RAW config list for this quadrant;
      * it is in the GATED list engine.brain.eligible_negotiation_actions()
        produces for this quadrant at this decision's own chosen rung (which
        additionally excludes both handoff flavors below rung 4);
      * the Action actually returned is the one that action maps to -- so a
        sample that had slipped through the gates could not quietly execute as
        something else either.
    """
    config = ev_config()
    decisions = explored_decisions()
    assert len(decisions) == EXPECTED_DECISIONS == 200

    for case in decisions:
        action, quadrant = case["action"], case["quadrant"]
        chosen = action.detail["negotiation_action"]
        raw = config["negotiation"]["eligible_actions"][quadrant]
        gated = brain.eligible_negotiation_actions(quadrant, case["plain"].rung, config)

        assert chosen in raw, f"{chosen} is not eligible for a {quadrant} buyer at all"
        assert chosen in gated, f"{chosen} is not reachable for a {quadrant} buyer today"
        assert action.detail["negotiation_selection"] == "explore"
        assert action.kind == EXPECTED_KIND[chosen], (
            f"sampled {chosen} but executed {action.kind}")


def test_two_hundred_explored_decisions_never_exceed_the_law_ceiling() -> None:
    """The invariant engine/brain.py's docstring states by construction:

        chosen == 0   OR   1 <= chosen <= available_rung

    Exploration changes WHICH action is taken, never at what rung: the rung is
    settled by the escalation walk before either EV branch runs. So an
    explored decision must land on exactly the rung the plain path chose --
    unless it is a handoff, which is rung 4 by definition and is only ever
    selectable once the plain path had already reached rung 4 itself.
    """
    for case in explored_decisions():
        action, ceiling = case["action"], case["ceiling"]
        assert action.available_rung == ceiling
        assert action.rung == 0 or 1 <= action.rung <= ceiling, (
            f"rung {action.rung} is above the law's ceiling of {ceiling}")

        if action.kind == brain.HANDOFF:
            assert case["plain"].kind == brain.HANDOFF, (
                "exploration made a case reach a human that the ordinary "
                "escalation walk would not have")
            assert action.rung == brain.HANDOFF_RUNG <= ceiling
        else:
            assert action.rung == case["plain"].rung, (
                "exploration moved the rung, which only the escalation walk may do")


# --------------------------------------------------------------------------
# ... and the guard that stops the two above being vacuous
# --------------------------------------------------------------------------

def test_exploration_actually_explores() -> None:
    """Both tests above would pass trivially against a sampler that always
    returned the argmax. This is what makes them mean something: across the
    same 200 decisions, more than one action is sampled, and the sample
    disagrees with the shipped EV pick a substantial share of the time."""
    decisions = explored_decisions()
    sampled = {case["action"].detail["negotiation_action"] for case in decisions}
    assert len(sampled) > 1, f"exploration never varied its choice: {sampled}"

    differed = sum(
        1 for case in decisions
        if case["action"].detail["negotiation_action"]
        != argmax_action(case["quadrant"], case["plain"].rung)
    )
    assert differed > len(decisions) // 4, (
        f"only {differed}/{len(decisions)} explored decisions differed from the "
        "argmax -- either the sampler is barely sampling, or the eligible lists "
        "have collapsed to a single option")


def test_a_quadrant_never_sees_an_action_its_config_row_withholds() -> None:
    """The specific over-escalation the eligible_actions table exists to
    prevent: a good_customer is never offered legal pressure, a
    can_pay_but_wont is never offered a payment plan -- however many times the
    sampler rolls."""
    config = ev_config()
    record = invoice(acceptance="2026-05-01")
    position = law.legal_position(record, TODAY)
    forbidden = {
        aw.GOOD_CUSTOMER: {negotiation.LEGAL_FACTS, negotiation.LEGAL_ESCALATION,
                           negotiation.COUNTER_SETTLE, negotiation.HUMAN_HANDOFF},
        aw.CAN_PAY_BUT_WONT: {negotiation.PAYMENT_PLAN},
    }
    for quadrant, banned in forbidden.items():
        for stream in range(100):
            action = brain.decide(
                record, buyer(), score_with_quadrant(quadrant), position, [], [],
                config=config, log=False, explore_rng=random.Random(stream),
            )
            assert action.detail["negotiation_action"] not in banned


def test_the_gate_override_flag_reports_what_actually_went_out() -> None:
    """When the label and the executed rung disagree, the record says so --
    and the Action itself still carries the EXECUTED rung, never the proposed
    one. This is the field sim/run_sim.py hands the attribution ledger, so a
    payment is always credited to what the buyer really received."""
    seen_override = False
    for case in explored_decisions():
        action = case["action"]
        proposed = action.detail["negotiation_proposed_rung"]
        override = action.detail["negotiation_gate_override"]
        assert override == (proposed is not None and proposed != action.rung)
        if override:
            seen_override = True
            assert action.rung == case["plain"].rung != proposed
    assert seen_override, (
        "no explored decision was overridden by a gate, so this test proved "
        "nothing about how overrides are recorded")


# --------------------------------------------------------------------------
# stop rules outrank exploration, exactly as they outrank everything else
# --------------------------------------------------------------------------

@pytest.mark.parametrize("case", ["opted_out", "paid", "disputed"])
def test_exploration_never_overrides_a_stop_rule(case: str) -> None:
    """Every stop rule sits above both EV branches in decide(), so an
    explore_rng cannot even be consulted for a case one of them catches. The
    absent negotiation_selection key is the proof it was never reached."""
    record = invoice(acceptance="2026-05-01")
    who = buyer()
    if case == "opted_out":
        who = buyer(opted_out=True)
    elif case == "paid":
        record = {**record, "status": "paid", "amount_paid_paise": record["amount_paise"],
                  "partial_payments": [{"date": "2026-06-01",
                                        "amount_paise": record["amount_paise"]}]}
    else:
        record = {**record, "disputed": True}

    position = law.legal_position(record, TODAY)
    for stream in range(20):
        action = brain.decide(
            record, who, score_with_quadrant(aw.CAN_PAY_BUT_WONT), position, [], [],
            config=ev_config(), log=False, explore_rng=random.Random(stream),
        )
        assert action.kind in (brain.STOP, brain.HANDOFF)
        assert "negotiation_selection" not in action.detail


# --------------------------------------------------------------------------
# and it cannot be reached from production
# --------------------------------------------------------------------------

def test_ev_mode_alone_is_still_argmax() -> None:
    """No key in config/rules.yaml turns exploration on. brain.ev_mode "on"
    with no explore_rng is the shipped argmax, unchanged."""
    record = invoice(acceptance="2026-05-01")
    action = brain.decide(record, buyer(), score_with_quadrant(aw.HIGH_RISK),
                          law.legal_position(record, TODAY), [], [],
                          config=ev_config(), log=False)
    assert action.detail["negotiation_selection"] == "argmax"
    assert action.detail["negotiation_action"] == argmax_action(aw.HIGH_RISK, action.rung)


def test_main_py_cannot_turn_exploration_on() -> None:
    """Exploration is simulator-only, and this is what keeps it that way:
    decide()'s switch is an OBJECT a caller has to construct, main.py never
    constructs one, and the score main.py passes carries no quadrant, so its
    EV branch is unreachable regardless of config."""
    import inspect
    from pathlib import Path

    root = Path(brain.__file__).resolve().parents[1]
    assert "explore" not in (root / "main.py").read_text(encoding="utf-8"), (
        "main.py must never mention exploration")

    default = inspect.signature(brain.decide).parameters["explore_rng"].default
    assert default is None, "exploration must be off unless a caller opts in"

    # The second, independent barrier: main.py's stage_brain feeds
    # engine.score.score_buyer() output, which carries no "quadrant" key, so
    # decide()'s ev_mode_on is False for it whatever config/rules.yaml says.
    from engine import score as score_engine
    assert "quadrant" not in inspect.getsource(score_engine.score_buyer)


# --------------------------------------------------------------------------
# end to end, through the simulator that actually uses it
# --------------------------------------------------------------------------

def test_a_real_exploration_run_records_what_was_executed(tmp_path, monkeypatch) -> None:
    """The whole thing, through sim/run_sim.py, against the file it writes.

    Everything above tests decide() in isolation. This runs the simulator the
    way a learning experiment would and checks the artifact that comes out:

      * every row's action_kind/rung is the EXECUTED action, and every row
        whose proposal was overridden proves it -- the recorded rung is the
        one the message actually went out at, not the one proposed;
      * every proposal is in that quadrant's own eligible_actions row;
      * no row exceeds the law ceiling, checked against the audit trail, which
        records available_rung for every single decision the run made.

    Points at its own outcomes file rather than the session-wide one conftest
    supplies, so it reads this run's rows and nothing else.
    """
    from engine import outcomes
    from sim import run_sim

    monkeypatch.setattr(outcomes, "OUTCOMES_PATH", tmp_path / "outcomes.jsonl")
    outcomes.start_file()
    report = run_sim.run_agent(42, 45, explore=True)

    assert report["explore"] is True
    # Exploration only exists on the EV path, so asking for it turns that on.
    assert report["ev_mode"] is True

    config = rules()
    rows = [row for row in outcomes.records(outcomes.OUTCOMES_PATH)
            if row["record_type"] == outcomes.ACTION_RECORD]
    assert rows, "the run recorded no actions at all"
    assert {row["mode"] for row in rows} == {"agent_ev_explore"}

    proposed_rows = [row for row in rows if row["proposed_action_kind"] is not None]
    assert proposed_rows, "no row carried a proposal, so nothing here is being tested"

    for row in proposed_rows:
        assert row["proposed_action_kind"] in \
            config["negotiation"]["eligible_actions"][row["quadrant"]]
        assert row["action_kind"] in (brain.SEND, brain.PAYMENT_PLAN,
                                      brain.COUNTER_SETTLE, brain.HANDOFF)
        assert row["gate_override"] == (
            row["proposed_rung"] is not None and row["proposed_rung"] != row["rung"])

    overridden = [row for row in proposed_rows if row["gate_override"]]
    assert overridden, "no gate override happened, so the executed-vs-proposed split is untested"
    for row in overridden:
        # The point of the whole exercise: the row says what went out.
        assert row["rung"] != row["proposed_rung"]

    # The ceiling, over every decision the run made -- not just the ones that
    # produced an outbound contact.
    entries = [entry for entry in audit.entries()
               if entry.get("actor") == "brain" and "available_rung" in entry.get("detail", {})]
    assert entries, "the audit trail recorded no brain decisions"
    for entry in entries:
        rung = entry["detail"]["rung"]
        ceiling = entry["detail"]["available_rung"]
        assert rung == 0 or 1 <= rung <= ceiling, (
            f"{entry.get('invoice_id')} acted at rung {rung} above its ceiling {ceiling}")
