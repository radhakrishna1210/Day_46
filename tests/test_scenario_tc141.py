"""docs/edge_cases.md TC-141 -- the scripted end-to-end scenario.

The scenario itself (sim/scenario_tc141.py) reuses the real pipeline; these
tests check the two properties CLAUDE.md's E4 task asked for by name, plus
that the whole 91-day run actually completes.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import audit, brain
from engine.config import rules
from sim import scenario_tc141


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    yield


def test_the_scenario_runs_end_to_end() -> None:
    result = scenario_tc141.run()
    assert result["narrative"]
    assert result["invoice"]["invoice_id"] == scenario_tc141.INVOICE_ID
    # Day 90's handoff is the scripted ending -- see the module docstring for
    # why the law's own ceiling, not a script, produces it.
    assert any("[handoff]" in line for line in result["narrative"])


def test_day61_injected_instruction_does_not_mark_the_invoice_paid() -> None:
    """TC-134's guarantee, exercised inside the full scenario rather than in
    isolation: nothing in the closed 5-intent schema can attach "mark paid"
    to anything, so the invoice's status is untouched by this reply.
    """
    result = scenario_tc141.run()
    invoice = result["invoice"]
    assert invoice["status"] != "paid"
    # Not fully settled either way: Day 53's Rs 50,000 is the only payment
    # this invoice ever receives.
    assert invoice["amount_paid_paise"] == 5_000_000


def test_day60_three_year_promise_is_never_recorded() -> None:
    """The E1 sanity-bound fix (config/rules.yaml promises.max_horizon_days),
    exercised end to end: apply_reply() only ever calls record_promise() when
    parse_reply() hands back an intent of "promise" with a resolved date --
    Day 60's absurd 3-year date is downgraded to "question" with date=None
    before apply_reply() ever sees it, so it must never reach the promise
    list at all, let alone as an open one.
    """
    result = scenario_tc141.run()
    promises_list = result["promises"]

    # Only Day 49's promise (pay by 2026-10-16) was ever recorded.
    assert len(promises_list) == 1
    assert promises_list[0]["promised_date"] == "2026-10-16"


def test_day60_three_year_promise_does_not_pause_recovery() -> None:
    """Re-derives the guarantee engine.brain.decide() actually relies on: no
    promise on file may buy the buyer silence all the way from Day 60 to
    Day 90 -- which it could only do if the 3-year date had been recorded as
    an open promise.
    """
    result = scenario_tc141.run()
    grace = int(rules()["ladder"]["promise_grace_days"])

    for day in (date(2026, 10, 26), date(2026, 11, 15), date(2026, 11, 22)):
        assert brain.active_promise(result["promises"], day, grace) is None
