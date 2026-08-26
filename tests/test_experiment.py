"""Day 9 -- the experiment: multi-seed proof and the report's data shape.

Non-negotiable #5: baseline vs agent on the SAME seeded data, honestly. This
file is what backs that claim -- not just for seed 42, but for two more
genuinely different synthetic worlds (data.generate.ensure_dataset is what
makes --seed N actually regenerate a different world instead of silently
reusing whatever was on disk; see its own tests in test_data.py's sibling
concerns).

Runs at a shorter horizon than the `--days 120` submission run to keep the
suite fast -- 60 days is enough for the ladder, promises and disputes to all
play out at least once per seed.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from data import generate, store
from report import build_report
from sim import run_sim

SEEDS = (42, 7, 2024)
DAYS = 60


@pytest.fixture(scope="module", autouse=True)
def _restore_seed_42_dataset_afterwards():
    """Multi-seed tests regenerate data/seed/ for other seeds -- leave it back

    on 42 when this module is done, so a manual `python sim/run_sim.py
    --seed 42` or another test module isn't surprised by a lingering seed.
    """
    yield
    generate.ensure_dataset(42)


# --------------------------------------------------------------------------
# the seed must actually control the world
# --------------------------------------------------------------------------

def test_ensure_dataset_regenerates_only_when_the_seed_differs() -> None:
    generate.ensure_dataset(42)
    assert store.load_meta()["seed"] == 42
    assert generate.ensure_dataset(42) is False        # already matches, no-op

    assert generate.ensure_dataset(7) is True
    assert store.load_meta()["seed"] == 7
    assert generate.ensure_dataset(7) is False

    generate.ensure_dataset(42)
    assert store.load_meta()["seed"] == 42


def test_both_agents_start_from_identical_invoice_sets() -> None:
    """Fairness precondition: --compare's two runs must see the same world."""
    _, baseline_invoices, baseline_personas, baseline_day0 = run_sim._load_world(42)
    _, agent_invoices, agent_personas, agent_day0 = run_sim._load_world(42)

    baseline_amounts = {inv["invoice_id"]: inv["amount_paise"] for inv in baseline_invoices}
    agent_amounts = {inv["invoice_id"]: inv["amount_paise"] for inv in agent_invoices}
    assert baseline_amounts == agent_amounts
    assert baseline_personas == agent_personas
    assert baseline_day0 == agent_day0


# --------------------------------------------------------------------------
# the multi-seed proof
# --------------------------------------------------------------------------

@pytest.fixture(scope="module", params=SEEDS, ids=lambda s: f"seed{s}")
def comparison(request):
    """One baseline + one agent run per seed -- this is also where money

    conservation gets checked: run_agent/run_baseline both call
    verify_conservation() before returning, so a real desync here would fail
    fixture setup, not just look wrong in an assertion downstream.
    """
    seed = request.param
    baseline = run_sim.run_baseline(seed, DAYS, verbose=False)
    agent = run_sim.run_agent(seed, DAYS, verbose=False)
    return seed, baseline, agent


def test_agent_beats_baseline_on_recovered_money(comparison) -> None:
    seed, baseline, agent = comparison
    assert agent["final"]["recovered_paise"] >= baseline["final"]["recovered_paise"], (
        f"seed {seed}: agent recovered {agent['final']['recovered_paise']} paise, "
        f"baseline recovered {baseline['final']['recovered_paise']} -- honestly worse, "
        f"not tuning the data to hide it"
    )


def test_agent_beats_baseline_on_the_fair_days_to_pay_comparison(comparison) -> None:
    """The RAW avg_days_to_pay per agent is not a fair comparison -- see

    sim.run_sim.matched_avg_days_to_pay's docstring: a run that gives up on
    hard invoices looks artificially fast because they never enter its
    average. The honest test is on the invoices BOTH runs actually recovered.
    """
    seed, baseline, agent = comparison
    matched = run_sim.matched_avg_days_to_pay(baseline, agent)
    if matched["n"] == 0:
        pytest.skip(f"seed {seed}: no invoices recovered by both runs within {DAYS} days")
    assert matched["agent"] <= matched["baseline"], (
        f"seed {seed}: on the {matched['n']} invoices both runs recovered, the "
        f"agent averaged {matched['agent']} days against the baseline's "
        f"{matched['baseline']} -- honestly worse, not tuning the data to hide it"
    )


# --------------------------------------------------------------------------
# results.json / report.html shape
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def results_payload():
    generate.ensure_dataset(42)
    baseline = run_sim.run_baseline(42, DAYS, verbose=False)
    agent = run_sim.run_agent(42, DAYS, verbose=False)
    matched = run_sim.matched_avg_days_to_pay(baseline, agent)
    return {
        "seed": 42, "days": DAYS, "generated": "2026-08-24T00:00:00",
        "baseline": baseline, "agent": agent, "matched_avg_days_to_pay": matched,
    }


def test_results_payload_is_json_serialisable(results_payload) -> None:
    """--compare writes exactly this shape to disk -- it has to round-trip."""
    text = json.dumps(results_payload)
    reloaded = json.loads(text)
    assert reloaded["seed"] == 42


def test_exceptions_list_is_the_agents_own_exceptions(results_payload) -> None:
    assert build_report.exceptions_list(results_payload) is results_payload["agent"]["exceptions"]


def test_per_rung_table_covers_rungs_one_to_three(results_payload) -> None:
    per_rung = results_payload["agent"]["per_rung"]
    assert set(per_rung) == {1, 2, 3}
    for row in per_rung.values():
        assert row["recovered_here"] <= row["invoices_contacted"]
        assert 0.0 <= row["effectiveness_pct"] <= 100.0


def test_per_attempt_table_covers_every_baseline_reminder(results_payload) -> None:
    per_attempt = results_payload["baseline"]["per_attempt"]
    assert set(per_attempt) == set(range(1, run_sim.BASELINE_MAX_MESSAGES + 1))
    for row in per_attempt.values():
        assert row["recovered_here"] <= row["invoices_contacted"]


def test_every_exception_row_has_a_reason_and_a_known_status(results_payload) -> None:
    for row in results_payload["agent"]["exceptions"]:
        assert row["reason"]
        assert row["status"] in {"open", "partially_paid", "disputed"}
        assert row["outstanding_paise"] >= 0


# --------------------------------------------------------------------------
# reply safety net -- docs/edge_cases.md TC-032 / TC-036
# --------------------------------------------------------------------------

def test_trip_wire_rows_reads_the_full_audit_trail_not_just_the_excerpt(monkeypatch) -> None:
    """Either action could easily fall outside build_report.AUDIT_EXCERPT_LINES."""
    from engine import audit

    fake_entries = [
        {"action": "reply_parsed", "invoice_id": "X", "detail": {}},
        {"action": "promise_may_contain_a_dispute", "invoice_id": "INV-1",
         "detail": {"reply": "goods damaged but I'll pay Friday"}},
        {"action": "promise_may_contain_multiple_amounts", "invoice_id": "INV-2",
         "detail": {"reply": "1 lakh Friday, 4 lakh next month"}},
    ]
    monkeypatch.setattr(audit, "entries", lambda: fake_entries)
    rows = build_report._trip_wire_rows()
    assert [r["invoice_id"] for r in rows] == ["INV-1", "INV-2"]
    assert "possible dispute" in rows[0]["flag"]
    assert "more than one amount" in rows[1]["flag"]


def test_trip_wire_rows_empty_when_nothing_flagged(monkeypatch) -> None:
    from engine import audit

    monkeypatch.setattr(audit, "entries", lambda: [{"action": "reply_parsed", "detail": {}}])
    assert build_report._trip_wire_rows() == []


def test_view_carries_trip_wires_for_the_template(results_payload) -> None:
    assert "trip_wires" in build_report._view(results_payload)


def test_build_report_writes_a_readable_html_file(results_payload, tmp_path) -> None:
    out = tmp_path / "report.html"
    path = build_report.build(results_payload, str(out))
    assert path == str(out)
    html = out.read_text(encoding="utf-8")
    assert "Baseline vs Agent" in html
    assert "Exceptions" in html
    assert "Audit trail excerpt" in html
    # every exception invoice id actually shows up in the rendered table
    for row in results_payload["agent"]["exceptions"][:5]:
        assert row["invoice_id"] in html


def test_build_report_handles_a_run_with_nothing_unrecovered(results_payload, tmp_path) -> None:
    payload = {**results_payload,
              "agent": {**results_payload["agent"], "exceptions": []}}
    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Every invoice was recovered" in html


def test_build_report_shows_a_clean_empty_state_for_the_buyer_panel(results_payload, tmp_path) -> None:
    """W2's empty state, same pattern as the exceptions test above: zero
    buyers with an outstanding invoice must render an honest empty message,
    not a broken template or a forced fixture standing in for a real run."""
    payload = {**results_payload,
              "agent": {**results_payload["agent"], "buyer_panel": []}}
    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "No buyer currently has an outstanding balance" in html


def test_build_report_renders_a_real_buyer_panel(results_payload, tmp_path) -> None:
    """The contrast case: seed 42's own agent run does leave buyers with an
    outstanding invoice, so the panel actually has rows to show, not just an
    empty state that would pass trivially either way."""
    assert results_payload["agent"]["buyer_panel"], "seed 42 should leave at least one buyer outstanding"
    out = tmp_path / "report.html"
    build_report.build(results_payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Buyer panel" in html
    first = results_payload["agent"]["buyer_panel"][0]
    assert (first["name"] or first["buyer_id"]) in html


def test_load_results_reports_a_clear_error_when_missing(tmp_path) -> None:
    with pytest.raises(build_report.ResultsMissing):
        build_report.load_results(tmp_path / "nope.json")


def test_build_report_works_after_a_real_json_round_trip(results_payload, tmp_path) -> None:
    """The real CLI path: results.json has STRING keys for per_rung/per_attempt

    (JSON has no integer object keys) -- prove the report still renders.
    """
    reloaded = json.loads(json.dumps(results_payload))
    out = tmp_path / "report.html"
    build_report.build(reloaded, str(out))
    html = out.read_text(encoding="utf-8")
    assert "soft nudge" in html
    assert "reminder 1" in html
