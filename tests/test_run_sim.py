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

from engine import audit, brain, llm
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
    # A frozen snapshot, taken immediately -- other tests in this module call
    # run_agent()/run_baseline() directly and clear/rewrite the real on-disk
    # audit log, so anything reading audit.entries() later would otherwise
    # see whichever run happened to run last, not this one.
    report["audit_snapshot"] = list(audit.entries())
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

    def spy(invoices, as_of, invalid_ids=frozenset()):
        calls.append(True)
        return original(invoices, as_of, invalid_ids)

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


# --- malformed invoices flow into the SAME exceptions mechanism ------------
# docs/edge_cases.md's own instruction: reuse the existing exceptions
# mechanism, don't invent a second one. These are unit tests of the plumbing
# (_totals, verify_conservation, _exceptions) rather than a full day-loop run,
# matching test_verify_conservation_catches_a_desynced_invoice's style above.

def test_a_malformed_invoice_surfaces_in_exceptions_with_its_reason() -> None:
    from datetime import date

    from engine import validate

    good = {
        "invoice_id": "INV-GOOD", "buyer_id": "BUY-01", "cohort": "current",
        "status": "open", "amount_paise": 1000, "amount_paid_paise": 0,
        "partial_payments": [], "acceptance_date": "2026-06-01",
        "written_agreement": False, "agreed_days": None, "disputed": False,
    }
    bad = {**good, "invoice_id": "INV-BAD", "acceptance_date": None}
    today = date(2026, 8, 25)

    reason_of = validate.reasons_for([good, bad], today)
    invalid_ids = frozenset(reason_of)
    rows = run_sim._exceptions([good, bad], {}, {}, reason_of, {}, today, invalid_ids)

    bad_row = next(r for r in rows if r["invoice_id"] == "INV-BAD")
    assert bad_row["days_overdue"] is None
    assert "acceptance date" in bad_row["reason"]
    good_row = next(r for r in rows if r["invoice_id"] == "INV-GOOD")
    assert good_row["days_overdue"] is not None


def test_totals_excludes_invalid_invoices_from_headline_money() -> None:
    from datetime import date

    good = {
        "invoice_id": "INV-GOOD", "cohort": "current", "status": "open",
        "amount_paise": 1000, "amount_paid_paise": 0, "partial_payments": [],
        "disputed": False,
    }
    bad = {**good, "invoice_id": "INV-BAD", "amount_paise": -500}
    today = date(2026, 8, 25)

    totals = run_sim._totals([good, bad], today, frozenset({"INV-BAD"}))
    assert totals["outstanding_paise"] == 1000     # the negative amount never counted


def test_a_duplicate_invoice_id_is_excluded_from_headline_totals_end_to_end() -> None:
    """docs/edge_cases.md TC-052, wired all the way through: audit_invalid()
    is the actual choke point run_agent()/run_baseline() call below -- proving
    validate.reasons_for() alone catches a duplicate (tests/test_validate.py)
    is not enough on its own if nothing carries that into invalid_ids here.
    """
    from datetime import date

    from engine import validate

    today = date(2026, 8, 25)
    dup_a = {
        "invoice_id": "INV-DUP", "cohort": "current", "status": "open",
        "amount_paise": 1000, "amount_paid_paise": 0, "partial_payments": [],
        "acceptance_date": "2026-06-01", "written_agreement": False,
        "agreed_days": None, "disputed": False,
    }
    dup_b = {**dup_a, "amount_paise": 5000}            # same invoice_id, different amount
    good = {**dup_a, "invoice_id": "INV-GOOD"}

    invalid_ids = frozenset(validate.audit_invalid([good, dup_a, dup_b], today, log=False))
    totals = run_sim._totals([good, dup_a, dup_b], today, invalid_ids)
    assert totals["outstanding_paise"] == 1000      # only INV-GOOD counted, neither duplicate


def test_a_transiently_future_dated_invoice_is_judged_normally_once_the_clock_passes_it(monkeypatch) -> None:
    """Regression: TC-050's own defect is clock-relative, unlike the other five.

    Freezing validity at day0 for the whole run would keep an invoice that
    becomes ordinary partway through permanently excluded from the headline
    totals and stuck with a stale "invalid" reason. It must instead be judged,
    by the end of the run, on whatever the agent actually did with it -- see
    run_agent()'s own comment on checking validity at last_day, not day0.
    """
    from datetime import date, timedelta

    from data import store as data_store
    from sim import personas as personas_module

    original_invoices = data_store.load_invoices()
    day0 = date.fromisoformat(data_store.load_meta()["simulation_start"])
    # A deadbeat buyer, so the fixture is still unpaid (and so still in the
    # exceptions list) by the time the 30-day window ends -- the point of
    # this test is what its VALIDITY looks like at the end, not whether it
    # got paid.
    persona_of = personas_module.load_hidden_personas()
    buyer_id = next(b for b, tag in persona_of.items() if tag == "deadbeat")
    future = (day0 + timedelta(days=5)).isoformat()

    fixture = {
        "invoice_id": "INV-TEST-TC050", "buyer_id": buyer_id, "cohort": "current",
        "description": "test", "po_number": None, "amount_paise": 5_000_000,
        "currency": "INR", "issue_date": future, "acceptance_date": future,
        "written_agreement": False, "agreed_days": None, "agreed_due_date": None,
        "status": "open", "partial_payments": [], "amount_paid_paise": 0,
        "paid_date": None, "disputed": False, "dispute_note": None, "promise_broken": False,
    }

    monkeypatch.setattr(data_store, "load_invoices", lambda: original_invoices + [fixture])
    report = run_sim.run_agent(seed=42, days=30, verbose=False)

    row = next(r for r in report["exceptions"] if r["invoice_id"] == "INV-TEST-TC050")
    assert row["days_overdue"] is not None             # no longer flagged invalid
    assert "invoice date" not in row["reason"]          # not the stale validation reason


def test_verify_conservation_skips_invalid_invoices_instead_of_crashing() -> None:
    from datetime import date

    bad = {
        "invoice_id": "INV-BAD", "cohort": "current", "status": "open",
        "amount_paise": -500, "amount_paid_paise": 0, "partial_payments": [],
    }
    # Would fail the "0 <= paid <= amount" assertion for a negative amount
    # if not excluded -- this is the TC-054 case reaching this function.
    run_sim.verify_conservation([bad], date(2026, 8, 25), frozenset({"INV-BAD"}))


# ============================================================================
# W3: buyer-level consolidation, wired into the day loop
# ============================================================================

def test_a_buyer_with_several_invoices_gets_one_bundled_message(agent_report) -> None:
    """Direct proof of the W3 Definition of Done: somewhere in a real seed-42
    run, one buyer's overdue invoices were covered by a single message."""
    bundled = [
        e for e in agent_report["audit_snapshot"]
        if e["actor"] == "writer" and len(e["detail"].get("bundle_invoice_ids", [])) > 1
    ]
    assert bundled, "no consolidated (multi-invoice) message was ever sent in this run"


def test_the_audit_trail_shows_the_decision_per_invoice_and_the_consolidated_send(
    agent_report,
) -> None:
    """The other half of the DoD: for a bundled message, every invoice it
    covers still has its OWN brain decision, writer draft and channel
    delivery in the audit trail -- consolidation changes the envelope count,
    never what gets recorded about each invoice."""
    entries = agent_report["audit_snapshot"]
    writer_entry = next(
        e for e in entries
        if e["actor"] == "writer" and len(e["detail"].get("bundle_invoice_ids", [])) > 1
    )
    bundle_ids = writer_entry["detail"]["bundle_invoice_ids"]
    for inv_id in bundle_ids:
        own = [e for e in entries if e["invoice_id"] == inv_id]
        assert any(e["actor"] == "brain" and e["action"] == "send" for e in own), (
            f"{inv_id} has no per-invoice brain decision in the audit trail")
        assert any(e["actor"] == "writer" for e in own), (
            f"{inv_id} has no writer entry in the audit trail")
        assert any(e["actor"] == "channels" for e in own), (
            f"{inv_id} has no channels entry in the audit trail")


def test_invoice_contacts_matches_the_pre_consolidation_messages_sent_semantics(
    agent_report,
) -> None:
    """invoice_contacts is what messages_sent counted before consolidation --
    every invoice-day that was part of a send, bundled or not. messages_sent
    now counts envelopes, so it must be <= invoice_contacts, and strictly <
    once real bundling has happened (proven above)."""
    assert agent_report["invoice_contacts"] >= agent_report["messages_sent"]
    assert agent_report["invoice_contacts"] > agent_report["messages_sent"]


def _dispute_onset(entries: list[dict]) -> dict[str, str]:
    """invoice_id -> the timestamp its FIRST dispute handoff was recorded.

    A dispute is the buyer's REACTION to a message that was itself sent
    earlier the same simulated day (persona.react() -> apply_reply() ->
    brain sees dispute_hold on its NEXT visit) -- so an invoice can
    legitimately appear in a writer bundle or a channel delivery on the very
    day it becomes disputed (that send happened before the reply existed),
    and the real invariant is that it must never appear again from that
    point on.
    """
    onset: dict[str, str] = {}
    for entry in entries:
        if entry["actor"] == "brain" and entry["action"] == "handoff" and "disputed" in entry["reason"]:
            onset.setdefault(entry["invoice_id"], entry["ts"])
    return onset


def test_a_disputed_invoice_never_appears_in_a_writer_bundle_after_the_dispute(
    agent_report,
) -> None:
    """The highest-risk guardrail in W3, proven end to end: once an invoice
    is disputed, it never again reaches a buyer through a consolidated
    message -- checked against every day AFTER the dispute took effect, not
    merely "this run never bundled it at all" (see _dispute_onset)."""
    entries = agent_report["audit_snapshot"]
    onset = _dispute_onset(entries)
    assert onset, "no disputed invoice occurred in this run to test against"
    violations = [
        (entry["invoice_id"], entry["ts"])
        for entry in entries if entry["actor"] == "writer"
        for inv_id in entry["detail"].get("bundle_invoice_ids", [])
        if inv_id in onset and entry["ts"] >= onset[inv_id]
    ]
    assert not violations, violations


def test_a_disputed_invoice_never_appears_in_a_channels_delivery_after_the_dispute(
    agent_report,
) -> None:
    entries = agent_report["audit_snapshot"]
    onset = _dispute_onset(entries)
    violations = [
        (entry["invoice_id"], entry["ts"])
        for entry in entries if entry["actor"] == "channels"
        and entry["invoice_id"] in onset and entry["ts"] >= onset[entry["invoice_id"]]
    ]
    assert not violations, violations


def test_every_send_decision_has_exactly_one_matching_writer_entry(agent_report) -> None:
    """The permanent structural guard behind W3's one-time before/after audit
    diff: that diff can only be run once (it needs the OLD, pre-consolidation
    code, which won't exist after this commit). What has to hold forever
    instead is this invariant, checked from a single run's own audit trail:
    every (invoice, day) where brain.decide() chose SEND has EXACTLY one
    writer entry for that same (invoice, day) -- never zero (a contact
    silently dropped by bundling) and never more than one (a contact
    silently duplicated). This is what "same invoice IDs contacted on the
    same days, just re-packaged into bundles" actually means, encoded as a
    test rather than a one-off diff.
    """
    entries = agent_report["audit_snapshot"]
    send_days = [(e["invoice_id"], e["ts"][:10]) for e in entries
                if e["actor"] == "brain" and e["action"] == "send"]
    writer_days = [(e["invoice_id"], e["ts"][:10]) for e in entries
                  if e["actor"] == "writer" and e["action"] in ("message_drafted", "writer_fallback")]

    assert send_days, "no send decision occurred in this run to test against"
    assert set(send_days) == set(writer_days), (
        set(send_days) ^ set(writer_days))

    from collections import Counter
    send_counts, writer_counts = Counter(send_days), Counter(writer_days)
    duplicated = {k: (send_counts[k], writer_counts[k])
                 for k in send_counts if send_counts[k] != writer_counts[k]}
    assert not duplicated, duplicated


def test_run_baseline_never_touches_consolidation_machinery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The baseline must stay a dumb, fixed-schedule bot completely untouched
    by W3 -- proven directly by making every consolidation entry point
    explode if run_baseline() ever reaches it, not merely by reading the
    code."""
    from engine import channels as channels_module
    from engine import consolidate as consolidate_module
    from engine import writer as writer_module

    def explode(*args, **kwargs):
        raise AssertionError("run_baseline() must never touch consolidation machinery")

    monkeypatch.setattr(consolidate_module, "bundle_sends", explode)
    monkeypatch.setattr(writer_module, "write_consolidated_message", explode)
    monkeypatch.setattr(channels_module, "send_consolidated", explode)

    report = run_sim.run_baseline(seed=42, days=30, verbose=False)
    assert report["messages_sent"] > 0
