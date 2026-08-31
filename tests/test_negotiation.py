"""Tests for the recovery probability + expected value model.

Phase 2's own thesis, proved in EV terms rather than in score terms: a
cash_flow_problem buyer should be offered a payment plan, not chased harder,
and a can_pay_but_wont buyer should see legal pressure, not a payment plan --
the opposite prescription for the opposite situation. Two structural guards
sit alongside the arithmetic tests: a tripwire that fails the moment
engine/brain.py starts reading this module (that is Phase 3's job), and a
no-cycle guard that keeps this module from ever importing engine.brain at
module scope, which would make Phase 3's brain-imports-negotiation direction
a circular import.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from engine import ability_willingness as aw
from engine import negotiation as neg
from engine.config import rules
from engine.money import round_paise

TODAY = date(2026, 8, 24)


def buyer(inflow: list[int] | None = None, failed: int = 0) -> dict:
    """A minimal buyer record, matching test_ability_willingness.py's own helper."""
    record = {"buyer_id": "BUY-01", "name": "Test Traders", "profile": "small_trader"}
    if inflow is not None:
        record["monthly_inflow_paise"] = inflow
    record["failed_payment_count"] = failed
    return record


def outstanding_invoice(amount_paise: int = 50_000_000, paid_paise: int = 0) -> dict:
    return {
        "invoice_id": "INV-CURRENT",
        "buyer_id": "BUY-01",
        "status": "overdue",
        "amount_paise": amount_paise,
        "amount_paid_paise": paid_paise,
    }


#: A steady, healthy inflow: no trend, no volatility -- same fixture as
#: test_ability_willingness.py, redefined here so this file has no test-to-test
#: import dependency.
HEALTHY = [50_000_000] * 8


# --- the action space and the config completeness it depends on -----------

def test_every_quadrant_action_pair_has_a_configured_probability() -> None:
    config = rules()["negotiation"]["recovery_probability"]
    for quadrant in aw.QUADRANTS:
        for action in neg.ACTIONS:
            assert action in config[quadrant], f"{quadrant}/{action} has no configured probability"


def test_every_action_has_a_configured_recovery_fraction() -> None:
    config = rules()["negotiation"]["recovery_fraction"]
    for action in neg.ACTIONS:
        assert action in config, f"{action} has no configured recovery_fraction"


# --- the phase's whole thesis, proved in EV terms --------------------------

def test_cash_flow_problem_prefers_a_payment_plan_over_messages_and_legal_escalation() -> None:
    """Wants to pay, cannot -- a payment plan should beat chasing harder."""
    ranked = neg.rank_actions(aw.CASH_FLOW_PROBLEM, 5_000_000_00)
    by_action = {r["action"]: r["ev_paise"] for r in ranked}
    assert by_action[neg.PAYMENT_PLAN] > by_action[neg.SOFT_NUDGE]
    assert by_action[neg.PAYMENT_PLAN] > by_action[neg.FIRM]
    assert by_action[neg.PAYMENT_PLAN] > by_action[neg.LEGAL_FACTS]
    assert by_action[neg.PAYMENT_PLAN] > by_action[neg.LEGAL_ESCALATION]
    assert ranked[0]["action"] == neg.PAYMENT_PLAN


def test_can_pay_but_wont_prefers_legal_pressure_over_a_payment_plan() -> None:
    """Could pay, is choosing not to -- the opposite prescription."""
    ranked = neg.rank_actions(aw.CAN_PAY_BUT_WONT, 5_000_000_00)
    by_action = {r["action"]: r["ev_paise"] for r in ranked}
    assert (by_action[neg.LEGAL_FACTS] > by_action[neg.PAYMENT_PLAN]
            or by_action[neg.LEGAL_ESCALATION] > by_action[neg.PAYMENT_PLAN])


# --- broken promises: only actions that depend on buyer follow-through ----

def test_broken_promises_lower_probability_only_for_the_configured_actions() -> None:
    applies_to = set(rules()["negotiation"]["promise_adjustment"]["applies_to"])
    for action in neg.ACTIONS:
        clean = neg.recovery_probability(aw.CAN_PAY_BUT_WONT, action)["probability"]
        pressured = neg.recovery_probability(
            aw.CAN_PAY_BUT_WONT, action, broken_promises=3)["probability"]
        if action in applies_to:
            assert pressured < clean, f"{action} should be penalised by broken promises"
        else:
            assert pressured == clean, f"{action} should be UNCHANGED by broken promises"


def test_human_handoff_and_legal_escalation_are_never_in_the_penalised_set() -> None:
    applies_to = set(rules()["negotiation"]["promise_adjustment"]["applies_to"])
    assert neg.HUMAN_HANDOFF not in applies_to
    assert neg.LEGAL_ESCALATION not in applies_to


# --- the breakdown-adds-up guarantee ---------------------------------------

def test_the_ev_breakdown_arithmetic_sums_exactly_to_ev_paise() -> None:
    for quadrant in aw.QUADRANTS:
        for action in neg.ACTIONS:
            result = neg.evaluate_action(
                action, quadrant=quadrant, outstanding_paise=12_345_601, broken_promises=2)
            assert sum(item["points"] for item in result["breakdown"]) == result["ev_paise"]


def test_the_probability_breakdown_arithmetic_sums_to_the_probability() -> None:
    for broken in (0, 1, 5):
        result = neg.recovery_probability(aw.CAN_PAY_BUT_WONT, neg.FIRM, broken_promises=broken)
        assert sum(item["points"] for item in result["breakdown"]) == pytest.approx(
            result["probability"], abs=0.5)


# --- rank_actions ordering ---------------------------------------------------

def test_rank_actions_is_sorted_descending_by_ev_paise() -> None:
    ranked = neg.rank_actions(aw.GOOD_CUSTOMER, 5_000_000_00)
    evs = [r["ev_paise"] for r in ranked]
    assert evs == sorted(evs, reverse=True)
    assert {r["action"] for r in ranked} == set(neg.ACTIONS)


def test_rank_actions_breaks_ties_by_action_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real config numbers essentially never tie by accident, so the tiebreak
    is proved with a forced tie rather than an observed one."""
    def tied(action: str, *, quadrant: str, outstanding_paise: int, broken_promises: int = 0) -> dict:
        return {"action": action, "ev_paise": 100, "probability": 50,
                "expected_recovery_paise": 0, "cost_paise": 0, "breakdown": []}

    monkeypatch.setattr(neg, "evaluate_action", tied)
    candidates = (neg.LEGAL_FACTS, neg.FIRM, neg.WAIT, neg.SOFT_NUDGE)
    ranked = neg.rank_actions(aw.GOOD_CUSTOMER, 1, candidates=candidates)
    assert [r["action"] for r in ranked] == sorted(candidates)


# --- the individual pieces --------------------------------------------------

def test_expected_recovery_paise_is_outstanding_times_the_configured_fraction() -> None:
    outstanding = 12_345_601
    for action in neg.ACTIONS:
        fraction = neg.recovery_fraction(action)
        assert neg.expected_recovery_paise(action, outstanding) == round_paise(outstanding * fraction)


def test_actions_with_a_full_recovery_fraction_recover_the_whole_outstanding_amount() -> None:
    for action in (neg.WAIT, neg.SOFT_NUDGE, neg.FIRM, neg.LEGAL_FACTS, neg.PAYMENT_PLAN):
        assert neg.recovery_fraction(action) == 1.0


def test_counter_settle_and_the_handoff_actions_recover_only_a_partial_fraction() -> None:
    for action in (neg.COUNTER_SETTLE, neg.HUMAN_HANDOFF, neg.LEGAL_ESCALATION):
        assert 0.0 < neg.recovery_fraction(action) < 1.0


def test_action_cost_paise_matches_the_documented_formula() -> None:
    config = rules()["negotiation"]["cost"]
    assert neg.action_cost_paise(neg.WAIT)["cost_paise"] == 0
    for action in (neg.SOFT_NUDGE, neg.FIRM, neg.LEGAL_FACTS, neg.PAYMENT_PLAN, neg.COUNTER_SETTLE):
        assert neg.action_cost_paise(action)["cost_paise"] == int(config["llm_call_paise"]["draft_message"])
    assert (neg.action_cost_paise(neg.HUMAN_HANDOFF)["cost_paise"]
            == int(config["human_minute_paise"]) * int(config["human_handoff_minutes"]))
    assert (neg.action_cost_paise(neg.LEGAL_ESCALATION)["cost_paise"]
            == int(config["human_minute_paise"]) * int(config["legal_escalation_minutes"]))


def test_recovery_probability_rejects_an_unknown_quadrant_or_action() -> None:
    with pytest.raises(ValueError):
        neg.recovery_probability("not_a_quadrant", neg.WAIT)
    with pytest.raises(ValueError):
        neg.recovery_probability(aw.GOOD_CUSTOMER, "not_an_action")


def test_recovery_fraction_and_action_cost_paise_reject_an_unknown_action() -> None:
    with pytest.raises(ValueError):
        neg.recovery_fraction("not_an_action")
    with pytest.raises(ValueError):
        neg.action_cost_paise("not_an_action")


# --- evaluate_invoice: composing two_axis_score with a ranking -------------

def test_evaluate_invoice_composes_the_two_axis_score_with_a_ranking() -> None:
    invoice = outstanding_invoice()
    result = neg.evaluate_invoice(buyer(HEALTHY), invoice, [], TODAY, broken_promises=0)
    for key in ("score", "confidence", "ability", "willingness", "quadrant"):
        assert key in result
    assert len(result["actions"]) == len(neg.ACTIONS)
    assert result["actions"] == neg.rank_actions(
        result["quadrant"], aw.outstanding_paise(invoice), broken_promises=0)


def test_evaluate_invoice_passes_broken_promises_through_to_the_ranking() -> None:
    invoice = outstanding_invoice()
    quiet = neg.evaluate_invoice(buyer(HEALTHY), invoice, [], TODAY, broken_promises=0)
    pressured = neg.evaluate_invoice(buyer(HEALTHY), invoice, [], TODAY, broken_promises=4)
    quiet_by_action = {r["action"]: r["probability"] for r in quiet["actions"]}
    pressured_by_action = {r["action"]: r["probability"] for r in pressured["actions"]}
    assert pressured_by_action[neg.FIRM] < quiet_by_action[neg.FIRM]


# --- edge case: nothing left to recover -------------------------------------

def test_a_zero_outstanding_invoice_ranks_wait_first_since_nothing_is_worth_spending_on() -> None:
    """docs/edge_cases.md TC-143: a settled invoice mistakenly asked for an EV ranking.

    Every other action costs something (an LLM call, or a human's minutes)
    and recovers a fraction of zero -- so every one of them scores a
    negative EV, and wait (cost 0, recovers 0) correctly comes out on top.
    """
    ranked = neg.rank_actions(aw.GOOD_CUSTOMER, 0)
    assert ranked[0]["action"] == neg.WAIT
    assert ranked[0]["ev_paise"] == 0
    for result in ranked[1:]:
        assert result["ev_paise"] <= 0


# --- explanations ------------------------------------------------------------

def test_explain_action_mentions_every_factor_that_produced_it() -> None:
    result = neg.evaluate_action(neg.LEGAL_FACTS, quadrant=aw.CAN_PAY_BUT_WONT,
                                 outstanding_paise=5_000_000)
    text = neg.explain_action(result)
    assert text.strip()
    for item in result["breakdown"]:
        assert item["factor"] in text


# --- the Phase 2 tripwire: the Brain must not consume this yet -------------

def test_the_brain_does_not_consume_negotiation_in_this_phase() -> None:
    """Phase 2 is compute-and-rank only; acting on it is Phase 3.

    The direct sibling of test_ability_willingness.py's Phase 1 tripwire --
    delete this one in Phase 3, exactly like its Phase 1 twin.
    """
    source = Path(__file__).resolve().parents[1] / "engine" / "brain.py"
    text = source.read_text(encoding="utf-8")
    assert "negotiation" not in text, "engine/brain.py now references 'negotiation' -- that is Phase 3"


# --- the no-cycle guard: negotiation.py must never import brain at module scope --

def test_negotiation_has_no_module_level_dependency_on_the_brain() -> None:
    """Phase 3 will have engine/brain.py import engine/negotiation.py. If this
    module ever imported engine.brain back at module scope, that would be a
    circular import -- so this AST-walks only the TOP-LEVEL statements (not
    inside main(), where a local import is fine and expected) and asserts
    none of them touch engine.brain.
    """
    source = Path(__file__).resolve().parents[1] / "engine" / "negotiation.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "engine.brain":
                pytest.fail("engine/negotiation.py imports engine.brain at module scope")
            if node.module == "engine" and any(alias.name == "brain" for alias in node.names):
                pytest.fail("engine/negotiation.py imports engine.brain "
                            "(via `from engine import brain`) at module scope")
        if isinstance(node, ast.Import):
            if any(alias.name == "engine.brain" for alias in node.names):
                pytest.fail("engine/negotiation.py imports engine.brain at module scope")


def test_the_no_cycle_guard_would_actually_catch_something() -> None:
    """Prove the AST check above is not vacuously passing."""
    tree = ast.parse("from engine import brain\n")
    caught = any(
        isinstance(node, ast.ImportFrom) and node.module == "engine"
        and any(alias.name == "brain" for alias in node.names)
        for node in tree.body
    )
    assert caught
