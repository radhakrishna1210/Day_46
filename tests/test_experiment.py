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


# --------------------------------------------------------------------------
# learned decisions -- the bandit's proposal vs. what the rules allowed
# (engine/brain.py writes these six keys into a decision's audit detail only
# when config/rules.yaml's learning.enabled is on -- ships off)
# --------------------------------------------------------------------------

_LEARNED_OVERRIDE = {
    "ts": "2026-07-01T00:00:00", "invoice_id": "INV-9", "buyer_id": "BUY-9",
    "actor": "brain", "action": "send", "reason": "rung 2 send", "source": "rule",
    "detail": {
        "learning_method": "posterior_mean", "estimated_probability": 0.62,
        "observations": 748, "bandit_top_choice": "legal_escalation",
        "executed_action": "firm", "gate_reason": "law_ceiling_rung_2",
    },
}
_LEARNED_CLEAN = {
    "ts": "2026-07-02T00:00:00", "invoice_id": "INV-10", "buyer_id": "BUY-10",
    "actor": "brain", "action": "send", "reason": "rung 2 send", "source": "rule",
    "detail": {
        "learning_method": "posterior_mean", "estimated_probability": 0.61,
        "observations": 748, "bandit_top_choice": "firm",
        "executed_action": "firm", "gate_reason": None,
    },
}


def test_learned_decision_rows_reads_the_full_trail_and_flags_overrides(monkeypatch) -> None:
    from engine import audit

    monkeypatch.setattr(audit, "entries", lambda: [
        {"action": "reply_parsed", "detail": {}}, _LEARNED_CLEAN, _LEARNED_OVERRIDE,
    ])
    rows = build_report._learned_decision_rows()
    assert [r["invoice_id"] for r in rows] == ["INV-10", "INV-9"]
    assert rows[0]["overridden"] is False and rows[0]["gate_reason"] is None
    assert rows[1]["overridden"] is True
    assert rows[1]["gate_reason"] == "law_ceiling_rung_2"
    assert rows[1]["bandit_top_choice"] == "legal_escalation"


def test_learned_decisions_excerpt_pulls_overrides_to_the_front(monkeypatch) -> None:
    from engine import audit

    clean = [{**_LEARNED_CLEAN, "invoice_id": f"C{i}"} for i in range(30)]
    monkeypatch.setattr(audit, "entries", lambda: clean + [_LEARNED_OVERRIDE])
    excerpt = build_report._learned_decisions_excerpt(build_report._learned_decision_rows())
    assert excerpt[0]["invoice_id"] == "INV-9"          # the override, first
    assert len(excerpt) == build_report.LEARNED_DECISION_EXCERPT_LINES


def test_view_carries_learned_decisions_and_ships_empty(results_payload) -> None:
    view = build_report._view(results_payload)
    assert "learned_decisions" in view
    # learning ships off, so a real --compare run records none
    assert view["learned_decisions"] == []
    assert view["learned_decisions_total"] == 0


def test_build_report_renders_the_learned_decisions_override_line(
    results_payload, tmp_path, monkeypatch,
) -> None:
    from engine import audit

    monkeypatch.setattr(audit, "entries", lambda: [_LEARNED_OVERRIDE])
    out = tmp_path / "report.html"
    build_report.build(results_payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Learned decisions" in html
    assert "law_ceiling_rung_2" in html
    assert "legal_escalation" in html


def test_build_report_shows_the_learned_decisions_empty_state(
    results_payload, tmp_path, monkeypatch,
) -> None:
    from engine import audit

    monkeypatch.setattr(audit, "entries", lambda: [{"action": "send", "detail": {}}])
    out = tmp_path / "report.html"
    build_report.build(results_payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Learning is off in this run" in html


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


# --------------------------------------------------------------------------
# W4 advisor item 2: per-seed edge-case counts in the multi-seed table
# --------------------------------------------------------------------------

def _multi_seed_payload(counts_by_seed):
    """A minimal results.json shape with just enough multi_seed structure
    for _multi_seed_rows() / _edge_case_note() to run against."""
    rows = [
        {"seed": seed, "baseline_recovered_paise": 0, "agent_recovered_paise": 0,
         "money_win": True, "matched_n": 0, "matched_baseline_days": None,
         "matched_agent_days": None, "days_win": False, **counts}
        for seed, counts in counts_by_seed.items()
    ]
    return {
        "multi_seed": {"rows": rows, "money_win_rate": "0/0", "days_win_rate": "n/a",
                       "days_excluded": 0},
    }


def test_multi_seed_rows_carries_edge_case_counts_through_to_the_view() -> None:
    payload = _multi_seed_payload({
        42: {"malformed_invoices": 0, "superseded_promise_invoices": 0},
        555: {"malformed_invoices": 1, "superseded_promise_invoices": 5},
    })
    rows = build_report._multi_seed_rows(payload)["rows"]
    assert rows[0]["malformed_invoices"] == 0
    assert rows[0]["superseded_promise_invoices"] == 0
    assert rows[1]["malformed_invoices"] == 1
    assert rows[1]["superseded_promise_invoices"] == 5


def test_multi_seed_rows_defaults_missing_edge_case_counts_to_zero() -> None:
    """A results.json written before W4's field existed must not crash the
    report -- it just shows zero, which is honest for "we didn't record it"
    even if not literally the same as "we checked and found none"."""
    payload = _multi_seed_payload({42: {}})
    rows = build_report._multi_seed_rows(payload)["rows"]
    assert rows[0]["malformed_invoices"] == 0
    assert rows[0]["superseded_promise_invoices"] == 0


def test_edge_case_note_summarises_across_seeds() -> None:
    payload = _multi_seed_payload({
        42: {"malformed_invoices": 0, "superseded_promise_invoices": 0},
        7: {"malformed_invoices": 0, "superseded_promise_invoices": 6},
        555: {"malformed_invoices": 1, "superseded_promise_invoices": 5},
    })
    note = build_report._edge_case_note(payload)
    assert "1 of 3 seeds" in note          # malformed
    assert "2 of 3" in note                # superseded
    assert "docs/edge_cases.md" in note    # points at the actual regression tests


def test_edge_case_note_is_none_without_a_multi_seed_run() -> None:
    assert build_report._edge_case_note({}) is None


def test_view_carries_the_edge_case_note_for_the_template(results_payload) -> None:
    assert "edge_case_note" in build_report._view(results_payload)


def test_build_report_renders_the_edge_case_columns_and_note(results_payload, tmp_path) -> None:
    """results_payload itself carries no multi_seed section (that fixture is
    the plain single-seed shape) -- build one for real, the same way
    sim/run_sim.py's own CLI does, so this exercises the actual code path
    rather than a hand-built fixture."""
    summary = run_sim.multi_seed_summary(
        42, results_payload["baseline"], results_payload["agent"], extra_seeds=(), days=DAYS)
    payload = {**results_payload, "multi_seed": summary}
    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Malformed invoices" in html
    assert "Superseded promises" in html


def test_load_results_reports_a_clear_error_when_missing(tmp_path) -> None:
    with pytest.raises(build_report.ResultsMissing):
        build_report.load_results(tmp_path / "nope.json")


# --------------------------------------------------------------------------
# Phase 4 Part B: the third (agent+EV) column, additive to results.json
# --------------------------------------------------------------------------

def test_headline_and_report_have_no_agent_ev_column_without_it(results_payload, tmp_path) -> None:
    """results_payload (this file's own fixture) carries no "agent_ev" key --
    the exact pre-Phase-4 shape. Every headline row must carry no "agent_ev"
    key either, and the rendered HTML must not mention the third column."""
    for row in build_report._headline_rows(results_payload):
        assert "agent_ev" not in row
    assert build_report._view(results_payload)["has_agent_ev"] is False
    assert build_report._view(results_payload)["ev_ablation_note"] is None

    out = tmp_path / "report.html"
    build_report.build(results_payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Agent + EV" not in html
    # The .agent-ev CSS rule is always in the static stylesheet -- what must
    # be absent is an actual rendered cell using it, not the class name itself.
    assert '"num agent-ev"' not in html


def test_headline_and_report_show_a_third_column_with_agent_ev(results_payload, tmp_path) -> None:
    """The contrast case: a real ev_mode: on run for the same seed, added the
    way sim/run_sim.py's own --compare CLI does, actually produces a third
    rendered column with real figures in it -- not just a flag with nothing
    behind it."""
    agent_ev = run_sim.run_agent(42, DAYS, verbose=False, ev_mode=True)
    payload = {**results_payload, "agent_ev": agent_ev}

    rows = build_report._headline_rows(payload)
    assert rows[0]["agent_ev"] == build_report._money(agent_ev["final"]["recovered_paise"])
    view = build_report._view(payload)
    assert view["has_agent_ev"] is True
    assert view["ev_ablation_note"] is not None

    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Agent + EV" in html
    assert build_report._money(agent_ev["final"]["recovered_paise"]) in html


def _agent_ev_multi_seed_payload() -> dict:
    """A minimal results.json shape with the ablation's own agent_ev_* keys
    on every row -- the same minimal-fixture style _multi_seed_payload()
    above uses for the W4 edge-case columns."""
    rows = [
        {"seed": seed, "baseline_recovered_paise": 100, "agent_recovered_paise": 150,
         "money_win": True, "matched_n": 0, "matched_baseline_days": None,
         "matched_agent_days": None, "days_win": False,
         "malformed_invoices": 0, "superseded_promise_invoices": 0,
         "agent_ev_recovered_paise": recovered, "agent_ev_money_win": win}
        for seed, recovered, win in ((42, 180, True), (7, 140, False))
    ]
    return {
        "multi_seed": {"rows": rows, "money_win_rate": "2/2", "days_win_rate": "n/a",
                       "days_excluded": 2, "agent_ev_money_win_rate": "1/2"},
    }


def test_multi_seed_rows_carries_agent_ev_columns_additively() -> None:
    payload = _agent_ev_multi_seed_payload()
    view = build_report._multi_seed_rows(payload)
    assert view["agent_ev_money_win_rate"] == "1/2"
    assert view["rows"][0]["agent_ev_recovered"] == build_report._money(180)
    assert view["rows"][0]["agent_ev_money_win"] is True
    assert view["rows"][1]["agent_ev_money_win"] is False
    # the existing columns are still exactly what they always were
    assert view["rows"][0]["agent_recovered"] == build_report._money(150)


def test_multi_seed_rows_without_agent_ev_has_no_agent_ev_keys() -> None:
    payload = _multi_seed_payload({42: {}})
    view = build_report._multi_seed_rows(payload)
    assert "agent_ev_money_win_rate" not in view
    assert not any(key.startswith("agent_ev_") for key in view["rows"][0])


def test_build_report_renders_the_agent_ev_multi_seed_columns(tmp_path) -> None:
    payload = {**_agent_ev_multi_seed_payload(), "seed": 42, "days": DAYS,
              "generated": "2026-08-24T00:00:00",
              "baseline": {"final": {"recovered_paise": 100, "outstanding_paise": 0,
                                     "disputed_paise": 0, "disputed_count": 0},
                          "avg_days_to_pay": None, "messages_sent": 0, "handoffs": 0,
                          "exceptions": [], "per_attempt": {}},
              "agent": {"final": {"recovered_paise": 150, "outstanding_paise": 0,
                                  "disputed_paise": 0, "disputed_count": 0},
                       "avg_days_to_pay": None, "messages_sent": 0, "handoffs": 0,
                       "exceptions": [], "per_rung": {}},
              "matched_avg_days_to_pay": {"n": 0, "baseline": None, "agent": None}}
    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "agent+EV" in html
    assert "₹ win vs agent" in html


# --------------------------------------------------------------------------
# the fourth (agent+EV+learned) column, additive to results.json on top of
# agent_ev -- same discipline as Phase 4 Part B above
# --------------------------------------------------------------------------

def test_headline_and_report_have_no_agent_learned_column_without_it(results_payload, tmp_path) -> None:
    """results_payload carries no "agent_learned" key -- the pre-fourth-arm
    shape. Every headline row must carry no "agent_learned" key either, and
    the rendered HTML must not mention the fourth column."""
    for row in build_report._headline_rows(results_payload):
        assert "agent_learned" not in row
    assert build_report._view(results_payload)["has_agent_learned"] is False
    assert build_report._view(results_payload)["learned_ablation_note"] is None

    out = tmp_path / "report.html"
    build_report.build(results_payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Agent + EV + Learned" not in html
    assert '"num agent-learned"' not in html


def test_headline_and_report_show_a_fourth_column_with_agent_learned(results_payload, tmp_path) -> None:
    """A real learned=True run for the same seed, added the way sim/run_sim.py's
    own --compare CLI does, actually produces a fourth rendered column with
    real figures in it, alongside the third (agent_ev) column."""
    agent_ev = run_sim.run_agent(42, DAYS, verbose=False, ev_mode=True)
    agent_learned = run_sim.run_agent(42, DAYS, verbose=False, learned=True)
    payload = {**results_payload, "agent_ev": agent_ev, "agent_learned": agent_learned}

    rows = build_report._headline_rows(payload)
    assert rows[0]["agent_learned"] == build_report._money(agent_learned["final"]["recovered_paise"])
    view = build_report._view(payload)
    assert view["has_agent_learned"] is True
    assert view["learned_ablation_note"] is not None

    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "Agent + EV + Learned" in html
    assert build_report._money(agent_learned["final"]["recovered_paise"]) in html


def _agent_learned_multi_seed_payload() -> dict:
    """A minimal results.json shape with the learned-posteriors ablation's own
    agent_learned_* keys on every row, ON TOP of agent_ev_* -- the same
    minimal-fixture style _agent_ev_multi_seed_payload() above uses. One seed
    is a win, one a loss -- proving the loss renders with no special-casing is
    the whole point of this fixture."""
    rows = [
        {"seed": seed, "baseline_recovered_paise": 100, "agent_recovered_paise": 150,
         "money_win": True, "matched_n": 0, "matched_baseline_days": None,
         "matched_agent_days": None, "days_win": False,
         "malformed_invoices": 0, "superseded_promise_invoices": 0,
         "agent_ev_recovered_paise": ev_recovered, "agent_ev_money_win": ev_win,
         "agent_learned_recovered_paise": learned_recovered, "agent_learned_money_win": learned_win}
        for seed, ev_recovered, ev_win, learned_recovered, learned_win in (
            (42, 180, True, 200, True), (7, 140, False, 120, False))
    ]
    return {
        "multi_seed": {"rows": rows, "money_win_rate": "2/2", "days_win_rate": "n/a",
                       "days_excluded": 2, "agent_ev_money_win_rate": "1/2",
                       "agent_learned_money_win_rate": "1/2",
                       "agent_learned_delta_paise": {"mean": 0, "min": -20, "max": 20, "n_seeds": 2}},
    }


def test_multi_seed_rows_carries_agent_learned_columns_additively() -> None:
    payload = _agent_learned_multi_seed_payload()
    view = build_report._multi_seed_rows(payload)
    assert view["agent_learned_money_win_rate"] == "1/2"
    assert view["rows"][0]["agent_learned_recovered"] == build_report._money(200)
    assert view["rows"][0]["agent_learned_money_win"] is True
    assert view["rows"][1]["agent_learned_money_win"] is False   # the loss, rendered like any other row
    # the existing agent_ev columns are still exactly what they always were
    assert view["rows"][0]["agent_ev_recovered"] == build_report._money(180)
    spread = view["agent_learned_spread"]
    assert spread["mean"] == build_report._money(0)
    assert spread["min"] == build_report._money(-20)
    assert spread["max"] == build_report._money(20)
    assert spread["n_seeds"] == 2


def test_multi_seed_rows_without_agent_learned_has_no_agent_learned_keys() -> None:
    payload = _multi_seed_payload({42: {}})
    view = build_report._multi_seed_rows(payload)
    assert "agent_learned_money_win_rate" not in view
    assert not any(key.startswith("agent_learned_") for key in view["rows"][0])


def test_build_report_renders_the_agent_learned_multi_seed_columns_and_the_loss(tmp_path) -> None:
    payload = {**_agent_learned_multi_seed_payload(), "seed": 42, "days": DAYS,
              "generated": "2026-08-24T00:00:00",
              "baseline": {"final": {"recovered_paise": 100, "outstanding_paise": 0,
                                     "disputed_paise": 0, "disputed_count": 0},
                          "avg_days_to_pay": None, "messages_sent": 0, "handoffs": 0,
                          "exceptions": [], "per_attempt": {}},
              "agent": {"final": {"recovered_paise": 150, "outstanding_paise": 0,
                                  "disputed_paise": 0, "disputed_count": 0},
                       "avg_days_to_pay": None, "messages_sent": 0, "handoffs": 0,
                       "exceptions": [], "per_rung": {}},
              "matched_avg_days_to_pay": {"n": 0, "baseline": None, "agent": None}}
    out = tmp_path / "report.html"
    build_report.build(payload, str(out))
    html = out.read_text(encoding="utf-8")
    assert "agent+EV+learned" in html
    assert "₹ win vs agent+EV" in html
    # the loss (seed 7) renders in the SAME table with the SAME "lose" class
    # as a win would -- no asterisk, no separate footnote, no hiding it.
    assert html.count('class="lose"') >= 1
    assert "small sample" in html


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
