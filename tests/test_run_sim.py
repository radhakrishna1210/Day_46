"""End-to-end tests for the simulator's day loop.

Runs at a shorter horizon than the `--days 120` Definition of Done check
(which is a manual `python sim/run_sim.py --seed 42 --days 120` run) so the
suite stays fast, but exercises the same machinery: real history threaded
across days, real promises maturing and breaking, and the money-conservation
invariant that must hold whatever happens to any single invoice.
"""

from __future__ import annotations

import os

import pytest

from engine import brain, llm
from sim import run_sim

#: Long enough for promises to mature (the replies.yaml date hints resolve
#: within days to a few weeks) and for backlog-bumped invoices to climb at
#: least one rung, short enough to keep the suite fast.
DAYS = 45


def test_forced_mock_mode_restores_whatever_env_had() -> None:
    os.environ["LLM_MODE"] = "live"
    with run_sim._forced_mock_mode():
        assert llm.get_mode() == "mock"
    assert os.environ["LLM_MODE"] == "live"
    del os.environ["LLM_MODE"]

    assert "LLM_MODE" not in os.environ
    with run_sim._forced_mock_mode():
        assert llm.get_mode() == "mock"
    assert "LLM_MODE" not in os.environ


def test_forced_mock_mode_actually_forces_it_not_just_relies_on_the_default() -> None:
    """Prove it, the way test_no_legal_constants.py proves its own guard."""
    os.environ["LLM_MODE"] = "live"
    try:
        assert llm.get_mode() == "live"          # the guard is needed
        with run_sim._forced_mock_mode():
            assert llm.get_mode() == "mock"       # and it works
    finally:
        os.environ.pop("LLM_MODE", None)


def test_the_rng_is_deterministic_and_independent_of_call_order() -> None:
    from datetime import date

    baseline = run_sim._rng(42, "INV-1", date(2026, 8, 24), "react").random()
    assert run_sim._rng(42, "INV-1", date(2026, 8, 24), "react").random() == baseline

    # A different invoice, day or tag is a different, unrelated stream.
    assert run_sim._rng(42, "INV-2", date(2026, 8, 24), "react").random() != baseline
    assert run_sim._rng(42, "INV-1", date(2026, 8, 25), "react").random() != baseline

    # Drawing from an unrelated stream first changes nothing: the same triple
    # always reproduces the same roll, independent of call order.
    run_sim._rng(99, "INV-9", date(2026, 1, 1), "keep").random()
    assert run_sim._rng(42, "INV-1", date(2026, 8, 24), "react").random() == baseline


@pytest.fixture(scope="module")
def agent_report():
    """One real run, traced for rung history so no test needs a second one."""
    seen: dict[str, set[int]] = {}
    original_decide = brain.decide

    def traced(*args, **kwargs):
        action = original_decide(*args, **kwargs)
        if action.kind == brain.SEND:
            seen.setdefault(action.invoice_id, set()).add(action.rung)
        return action

    brain.decide = traced
    try:
        report = run_sim.run_agent(seed=42, days=DAYS, verbose=True)
    finally:
        brain.decide = original_decide
    report["rungs_seen_by_invoice"] = seen
    return report


@pytest.fixture(scope="module")
def baseline_report():
    return run_sim.run_baseline(seed=42, days=DAYS, verbose=True)


def test_run_agent_completes_and_conserves_money(agent_report) -> None:
    final = agent_report["final"]
    assert final["recovered_paise"] >= 0
    assert final["outstanding_paise"] >= 0
    assert agent_report["messages_sent"] > 0
    assert agent_report["handoffs"] + agent_report["stops"] > 0


def test_run_baseline_completes_and_conserves_money(baseline_report) -> None:
    final = baseline_report["final"]
    assert final["recovered_paise"] >= 0
    assert final["outstanding_paise"] >= 0
    assert baseline_report["messages_sent"] > 0
    # The dumb bot never escalates to a human -- it does not know how.
    assert baseline_report["handoffs"] == 0


def test_llm_mode_in_the_environment_is_unaffected_by_a_full_run(monkeypatch) -> None:
    monkeypatch.setenv("LLM_MODE", "live")
    run_sim.run_agent(seed=42, days=5, verbose=False)
    assert os.environ["LLM_MODE"] == "live"


def test_a_forgetful_buyer_pays_after_a_single_message(agent_report) -> None:
    assert any(
        "forgetful" in line and "paid in full after the rung-" in line
        for line in agent_report["narrative"]
    )


def test_a_deadbeat_hits_a_hard_stop_or_handoff(agent_report) -> None:
    assert any(
        "deadbeat" in line and ("handoff" in line or "stop" in line)
        for line in agent_report["narrative"]
    )


def test_at_least_one_invoice_climbs_more_than_one_rung(agent_report) -> None:
    """A real multi-rung climb, traced independently of the narrative text."""
    climbers = {inv: rungs for inv, rungs in agent_report["rungs_seen_by_invoice"].items()
               if len(rungs) >= 2}
    assert climbers, "no invoice was ever sent messages at more than one rung"


def test_agent_recovers_at_least_as_much_as_the_baseline(agent_report, baseline_report) -> None:
    """Not a strict inequality per invoice -- see the exceptions list on Day 9

    -- but across the whole seeded batch the smarter agent should not trail
    a bot that never uses any legal leverage or promise memory.
    """
    assert agent_report["final"]["recovered_paise"] >= baseline_report["final"]["recovered_paise"]


def test_conservation_is_checked_on_every_run(monkeypatch) -> None:
    """run_agent/run_baseline both call verify_conservation() before returning --

    prove that a real desync would actually be caught, the way
    test_no_legal_constants.py proves its own guards fire.
    """
    calls = []
    original = run_sim.verify_conservation

    def spy(invoices, as_of):
        calls.append(True)
        return original(invoices, as_of)

    monkeypatch.setattr(run_sim, "verify_conservation", spy)
    run_sim.run_agent(seed=42, days=5, verbose=False)
    assert calls, "run_agent must verify conservation before returning"


def test_verify_conservation_catches_a_desynced_invoice() -> None:
    from datetime import date

    invoice = {
        "invoice_id": "INV-FAKE", "cohort": "current", "amount_paise": 1000,
        "amount_paid_paise": 400, "partial_payments": [{"date": "2026-08-24", "amount_paise": 300}],
        "status": "partially_paid", "acceptance_date": "2026-08-01",
        "written_agreement": False, "agreed_days": None,
    }
    with pytest.raises(AssertionError):
        run_sim.verify_conservation([invoice], date(2026, 8, 24))
