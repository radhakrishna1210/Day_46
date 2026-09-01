"""Tests for outcome attribution.

The whole module is one rule -- "credit a payment to the most recent action
within the horizon" -- so these tests are mostly the rule's own boundaries:
inside the horizon, outside it, two actions competing for one payment, a
payment with no action to claim it, and an action nothing ever paid. The last
two are the ones worth having: a naive attributor gets the happy path right
and quietly loses the failures and the unattributed money, which is exactly
what would make the resulting numbers flattering and wrong.

Every ledger here is built with an explicit `horizon=` rather than the config
default, so a future edit to config/rules.yaml's learning block changes the
shipped policy without silently rewriting what these tests mean. One separate
test pins that the default really does come from config.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from engine import outcomes
from engine.config import rules

DAY0 = date(2026, 8, 24)


def ledger(horizon: int = 14) -> outcomes.OutcomeLedger:
    return outcomes.OutcomeLedger(mode="test", seed=42, horizon=horizon)


def act(led: outcomes.OutcomeLedger, day_offset: int, *, invoice: str = "INV-1",
        kind: str = "send", rung: int = 1, quadrant: str | None = "good_customer",
        outstanding: int = 1_000_000):
    return led.record_action(
        invoice_id=invoice, buyer_id="BUY-01", day=DAY0 + timedelta(days=day_offset),
        action_kind=kind, rung=rung, outstanding_paise_at_action=outstanding,
        quadrant=quadrant,
    )


def pay(led: outcomes.OutcomeLedger, day_offset: int, amount: int = 500_000,
        *, invoice: str = "INV-1"):
    return led.record_payment(invoice_id=invoice, day=DAY0 + timedelta(days=day_offset),
                              amount_paise=amount)


# --------------------------------------------------------------------------
# the five cases the rule has to get right
# --------------------------------------------------------------------------

def test_a_payment_inside_the_horizon_is_credited_to_the_action() -> None:
    led = ledger(horizon=14)
    act(led, 0)
    pay(led, 10, 500_000)

    result = led.attribute()
    record = result["records"][0]
    assert record["paid_within_horizon"] is True
    assert record["paise_recovered_within_horizon"] == 500_000
    assert result["unattributed"] == []
    assert result["summary"]["payments_attributed"] == 1


def test_a_payment_on_the_horizon_boundary_still_counts() -> None:
    """Day 14 with a 14-day horizon is INSIDE it -- the comparison is <=, not
    <. Pinned because an off-by-one here silently reclassifies real wins as
    unattributed money, and nothing else in the suite would notice."""
    led = ledger(horizon=14)
    act(led, 0)
    pay(led, 14, 700_000)

    result = led.attribute()
    assert result["records"][0]["paid_within_horizon"] is True
    assert result["records"][0]["paise_recovered_within_horizon"] == 700_000


def test_a_same_day_payment_is_credited_to_that_days_action() -> None:
    """A persona paying the moment the message lands is the single most
    common success in the simulator -- distance 0 must be inside."""
    led = ledger(horizon=14)
    act(led, 3)
    pay(led, 3, 900_000)

    assert led.attribute()["records"][0]["paise_recovered_within_horizon"] == 900_000


def test_a_payment_outside_the_horizon_leaves_the_action_a_failure() -> None:
    led = ledger(horizon=14)
    act(led, 0)
    pay(led, 15, 500_000)

    result = led.attribute()
    record = result["records"][0]
    assert record["paid_within_horizon"] is False
    assert record["paise_recovered_within_horizon"] == 0
    # And the money is not lost -- it becomes an unattributed payment, which
    # is the whole point of having that bucket at all.
    assert [p["amount_paise"] for p in result["unattributed"]] == [500_000]
    assert result["summary"]["paise_unattributed"] == 500_000


def test_two_actions_inside_one_horizon_credit_the_most_recent() -> None:
    led = ledger(horizon=14)
    first = act(led, 0, rung=1)
    second = act(led, 5, rung=2)
    pay(led, 8, 400_000)

    records = {r["rung"]: r for r in led.attribute()["records"]}
    assert first.day < second.day
    assert records[2]["paid_within_horizon"] is True
    assert records[2]["paise_recovered_within_horizon"] == 400_000
    assert records[1]["paid_within_horizon"] is False
    assert records[1]["paise_recovered_within_horizon"] == 0


def test_two_actions_on_the_same_day_credit_the_one_recorded_last() -> None:
    """"Most recent" has to be a total order, not a tie the code breaks by
    luck -- the ledger's own sequence number is what settles a same-day pair."""
    led = ledger(horizon=14)
    act(led, 4, rung=1)
    act(led, 4, rung=3)
    pay(led, 4, 250_000)

    records = {r["rung"]: r for r in led.attribute()["records"]}
    assert records[3]["paise_recovered_within_horizon"] == 250_000
    assert records[1]["paise_recovered_within_horizon"] == 0


def test_a_payment_with_no_action_before_it_is_recorded_as_unattributed() -> None:
    """A buyer who pays on their own. Never dropped, never handed to the
    nearest action -- claiming it would overstate what the agent achieved."""
    led = ledger(horizon=14)
    pay(led, 6, 850_000)

    result = led.attribute()
    assert result["records"] == []
    assert len(result["unattributed"]) == 1
    entry = result["unattributed"][0]
    assert entry["record_type"] == outcomes.UNATTRIBUTED_PAYMENT_RECORD
    assert entry["invoice_id"] == "INV-1"
    assert entry["amount_paise"] == 850_000
    assert "14 day(s)" in entry["reason"]
    assert result["summary"]["payments_unattributed"] == 1
    assert result["summary"]["paise_unattributed"] == 850_000


def test_a_payment_before_any_action_on_that_invoice_is_unattributed() -> None:
    """The action came AFTER the money -- chasing an invoice the buyer had
    already settled that morning. The horizon looks backwards only."""
    led = ledger(horizon=14)
    pay(led, 2, 300_000)
    act(led, 2)

    result = led.attribute()
    assert result["records"][0]["paid_within_horizon"] is False
    assert len(result["unattributed"]) == 1


def test_an_action_with_no_payment_at_all_is_recorded_as_a_failure() -> None:
    """The failure line still gets written. A file of successes only would
    make every action look like it worked."""
    led = ledger(horizon=14)
    act(led, 0, outstanding=2_500_000)

    result = led.attribute()
    assert len(result["records"]) == 1
    record = result["records"][0]
    assert record["paid_within_horizon"] is False
    assert record["paise_recovered_within_horizon"] == 0
    assert record["outstanding_paise_at_action"] == 2_500_000
    assert result["unattributed"] == []
    assert result["summary"]["actions_paid_within_horizon"] == 0


# --------------------------------------------------------------------------
# scoping: one payment, one action, one invoice
# --------------------------------------------------------------------------

def test_a_payment_never_credits_an_action_on_a_different_invoice() -> None:
    led = ledger(horizon=14)
    act(led, 0, invoice="INV-1")
    pay(led, 1, 500_000, invoice="INV-2")

    result = led.attribute()
    assert result["records"][0]["paise_recovered_within_horizon"] == 0
    assert result["unattributed"][0]["invoice_id"] == "INV-2"


def test_one_payment_is_credited_to_exactly_one_action() -> None:
    """Two actions inside the horizon must not both book the same rupees --
    summing the column would then overstate total recovery."""
    led = ledger(horizon=14)
    act(led, 0)
    act(led, 3)
    pay(led, 4, 600_000)

    result = led.attribute()
    total = sum(r["paise_recovered_within_horizon"] for r in result["records"])
    assert total == 600_000
    assert result["summary"]["paise_attributed"] == 600_000


def test_several_payments_inside_one_horizon_all_credit_that_action() -> None:
    led = ledger(horizon=14)
    act(led, 0)
    pay(led, 2, 100_000)
    pay(led, 9, 250_000)

    record = led.attribute()["records"][0]
    assert record["paid_within_horizon"] is True
    assert record["paise_recovered_within_horizon"] == 350_000


# --------------------------------------------------------------------------
# the record shape
# --------------------------------------------------------------------------

def test_the_record_carries_every_field_the_attribution_is_meant_to_explain() -> None:
    led = outcomes.OutcomeLedger(mode="test", seed=42, horizon=14, run_id="RUN-1")
    act(led, 0, invoice="INV-9", kind="payment_plan", rung=2,
        quadrant="cash_flow_problem", outstanding=4_200_000)
    pay(led, 1, 4_200_000, invoice="INV-9")

    record = led.attribute()["records"][0]
    assert record == {
        "record_type": outcomes.ACTION_RECORD,
        "run_id": "RUN-1",
        "mode": "test",
        "seed": 42,
        "invoice_id": "INV-9",
        "buyer_id": "BUY-01",
        "day": DAY0.isoformat(),
        "quadrant": "cash_flow_problem",
        "action_kind": "payment_plan",
        "rung": 2,
        "outstanding_paise_at_action": 4_200_000,
        "paid_within_horizon": True,
        "paise_recovered_within_horizon": 4_200_000,
    }


def test_a_run_with_no_two_axis_score_writes_quadrant_null_not_a_guess() -> None:
    led = ledger(horizon=14)
    act(led, 0, quadrant=None)

    record = led.attribute()["records"][0]
    assert record["quadrant"] is None
    assert json.loads(json.dumps(record))["quadrant"] is None


def test_records_keep_the_order_the_actions_were_taken_in() -> None:
    led = ledger(horizon=14)
    act(led, 0, invoice="INV-1")
    act(led, 1, invoice="INV-2")
    act(led, 2, invoice="INV-1")

    ids = [r["invoice_id"] for r in led.attribute()["records"]]
    assert ids == ["INV-1", "INV-2", "INV-1"]


# --------------------------------------------------------------------------
# config and the file on disk
# --------------------------------------------------------------------------

def test_the_horizon_comes_from_config_and_is_not_hardcoded() -> None:
    assert outcomes.horizon_days() == int(rules()["learning"]["attribution_horizon_days"])
    assert outcomes.OutcomeLedger(mode="test", seed=1).horizon == outcomes.horizon_days()


def test_horizon_days_reads_a_caller_supplied_config() -> None:
    assert outcomes.horizon_days({"learning": {"attribution_horizon_days": 3}}) == 3


def test_a_shorter_horizon_really_does_narrow_what_counts() -> None:
    """The config key is not decoration -- it changes the verdict."""
    def run(horizon: int) -> bool:
        led = ledger(horizon=horizon)
        act(led, 0)
        pay(led, 5, 500_000)
        return led.attribute()["records"][0]["paid_within_horizon"]

    assert run(14) is True
    assert run(3) is False


def test_write_produces_one_json_object_per_line_including_the_failures(tmp_path) -> None:
    path = tmp_path / "outcomes.jsonl"
    led = ledger(horizon=14)
    act(led, 0, invoice="INV-1")          # paid
    act(led, 0, invoice="INV-2")          # never paid
    pay(led, 1, 500_000, invoice="INV-1")
    pay(led, 1, 900_000, invoice="INV-3")  # unattributed
    led.write(path=path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    kinds = [line["record_type"] for line in lines]
    assert kinds == [outcomes.ACTION_RECORD, outcomes.ACTION_RECORD,
                     outcomes.UNATTRIBUTED_PAYMENT_RECORD]
    assert outcomes.records(path=path) == lines


def test_write_never_truncates_so_one_invocations_runs_accumulate(tmp_path) -> None:
    """A --compare is one experiment run eighteen times; its arms belong in
    one file. write() appending is what makes that work."""
    path = tmp_path / "outcomes.jsonl"
    outcomes.start_file(path)

    first = outcomes.OutcomeLedger(mode="baseline", seed=42, horizon=14, run_id="R1")
    first.record_action(invoice_id="INV-1", buyer_id="BUY-01", day=DAY0,
                        action_kind="reminder", rung=1, outstanding_paise_at_action=1)
    first.write(path=path)
    second = outcomes.OutcomeLedger(mode="agent", seed=42, horizon=14, run_id="R2")
    second.record_action(invoice_id="INV-1", buyer_id="BUY-01", day=DAY0,
                         action_kind="send", rung=1, outstanding_paise_at_action=1)
    second.write(path=path)

    assert [r["run_id"] for r in outcomes.records(path=path)] == ["R1", "R2"]


def test_start_file_is_the_only_thing_that_truncates(tmp_path) -> None:
    """The bug this replaced: truncation used to happen implicitly on the
    first write of a PROCESS, so a pytest run calling run_agent() several
    times stacked every one of them into the production file. Truncation is
    now one explicit call that only sim/run_sim.py's main() makes."""
    path = tmp_path / "outcomes.jsonl"
    path.write_text('{"record_type": "stale"}\n', encoding="utf-8")

    # A write on its own leaves the stale row exactly where it was.
    stale_survivor = outcomes.OutcomeLedger(mode="agent", seed=1, horizon=14, run_id="R1")
    stale_survivor.record_action(invoice_id="INV-1", buyer_id="BUY-01", day=DAY0,
                                 action_kind="send", rung=1, outstanding_paise_at_action=1)
    stale_survivor.write(path=path)
    assert any(r["record_type"] == "stale" for r in outcomes.records(path=path))

    # start_file() is what clears it, and it leaves an empty file behind.
    outcomes.start_file(path)
    assert outcomes.records(path=path) == []
    assert path.exists()


def test_clear_removes_the_file_outright(tmp_path) -> None:
    path = tmp_path / "outcomes.jsonl"
    led = ledger(horizon=14)
    act(led, 0)
    led.write(path=path)
    assert path.exists()

    outcomes.clear(path=path)
    assert not path.exists()
    assert outcomes.records(path=path) == []


# --------------------------------------------------------------------------
# run_id: which execution wrote these bytes
# --------------------------------------------------------------------------

def test_run_id_names_the_seed_and_the_mode() -> None:
    led = outcomes.OutcomeLedger(mode="agent_ev", seed=7)
    assert led.run_id.startswith("7_agent_ev_")


def test_run_id_is_on_every_row_including_unattributed_payments(tmp_path) -> None:
    path = tmp_path / "outcomes.jsonl"
    outcomes.start_file(path)
    led = outcomes.OutcomeLedger(mode="agent", seed=42, horizon=14, run_id="R1")
    act(led, 0, invoice="INV-1")
    pay(led, 1, 100, invoice="INV-1")
    pay(led, 1, 200, invoice="INV-2")   # unattributed
    led.write(path=path)

    rows = outcomes.records(path=path)
    assert {r["record_type"] for r in rows} == {outcomes.ACTION_RECORD,
                                                outcomes.UNATTRIBUTED_PAYMENT_RECORD}
    assert all(r["run_id"] == "R1" for r in rows)
    assert all(r["seed"] == 42 for r in rows)


def test_two_identical_runs_still_get_different_run_ids() -> None:
    """run_id's whole job is separating two executions that are otherwise
    byte-identical -- if the same seed and mode collided it would be useless
    for exactly the case it exists for."""
    ids = {outcomes.OutcomeLedger(mode="agent", seed=42).run_id for _ in range(50)}
    assert len(ids) == 50


def test_run_id_stays_out_of_the_summary_so_identical_runs_compare_equal() -> None:
    """tests/test_run_sim.py requires two identical run_agent() calls to
    compare equal, and the summary rides along in each arm's report. A
    wall-clock id in there would break that guarantee."""
    first = outcomes.OutcomeLedger(mode="agent", seed=42, horizon=14).attribute()["summary"]
    second = outcomes.OutcomeLedger(mode="agent", seed=42, horizon=14).attribute()["summary"]
    assert first == second
    assert "run_id" not in first
    assert first["seed"] == 42 and first["mode"] == "agent"


def test_runs_reports_what_the_file_actually_holds(tmp_path) -> None:
    """The provenance check that would have caught today's confusion in one
    line instead of after a full reconciliation."""
    path = tmp_path / "outcomes.jsonl"
    outcomes.start_file(path)
    for seed, mode in ((42, "baseline"), (42, "agent"), (7, "agent")):
        led = outcomes.OutcomeLedger(mode=mode, seed=seed, horizon=14,
                                     run_id=f"{seed}_{mode}")
        act(led, 0)
        led.write(path=path)

    found = outcomes.runs(path)
    assert set(found) == {"42_baseline", "42_agent", "7_agent"}
    assert {r["seed"] for r in found.values()} == {42, 7}
    assert all(r["action_rows"] == 1 for r in found.values())


# --------------------------------------------------------------------------
# end to end: the file holds exactly the runs that happened, nothing else
# --------------------------------------------------------------------------

def test_grouping_a_real_files_rows_by_seed_gives_exactly_the_seeds_run(
    tmp_path, monkeypatch,
) -> None:
    """The guarantee today's confusion cost us. Runs the REAL simulator for
    two seeds and three arms into one fresh file, then asks the file what it
    holds -- it must name those seeds and those arms and nothing else, with
    no rows stacked in from any other process or any earlier run.

    Deliberately not a mock: the failure being guarded against was in the
    plumbing between run_agent() and the file, which a fake ledger would not
    have exercised at all.
    """
    from data import generate
    from engine import audit
    from sim import run_sim

    path = tmp_path / "outcomes.jsonl"
    # Seeded with a row from an imaginary other process, to prove start_file()
    # clears what it found rather than appending underneath it.
    path.write_text('{"record_type": "action", "run_id": "GHOST", "mode": "agent", '
                    '"seed": 999}\n', encoding="utf-8")
    # Point the module default at it, exactly as a real invocation would --
    # run_agent()/run_baseline() take no path argument, so going through the
    # same global they really use is the whole point of this test.
    monkeypatch.setattr(outcomes, "OUTCOMES_PATH", path)

    trail = audit.snapshot()
    try:
        outcomes.start_file()           # what sim/run_sim.py's main() does, once
        for seed in (42, 7):
            run_sim.run_baseline(seed, days=6)
            run_sim.run_agent(seed, days=6)
            run_sim.run_agent(seed, days=6, ev_mode=True)
    finally:
        generate.ensure_dataset(42)     # leave the dataset as we found it
        audit.restore(trail)            # and the audit trail with it

    found = outcomes.runs()
    rows = outcomes.records()

    # Exactly the seeds actually run -- not a superset, not a subset.
    assert sorted({r["seed"] for r in rows}) == [7, 42]
    # Exactly the six runs actually made, one run_id each.
    assert len(found) == 6
    assert sorted((r["seed"], r["mode"]) for r in found.values()) == [
        (7, "agent"), (7, "agent_ev"), (7, "baseline"),
        (42, "agent"), (42, "agent_ev"), (42, "baseline"),
    ]
    # And nothing stacked in from anywhere else.
    assert not any(r["run_id"] == "GHOST" for r in rows)
    assert all(r["seed"] in (7, 42) for r in rows)
    assert len({r["run_id"] for r in rows}) == 6


def test_the_suite_never_writes_the_production_outcomes_file() -> None:
    """conftest.py's session fixture, asserted rather than assumed -- it is
    half the fix, and a silently-dropped autouse fixture would put the suite
    straight back to appending into audit/outcomes.jsonl."""
    assert outcomes.OUTCOMES_PATH != outcomes.ROOT / "audit" / "outcomes.jsonl"
    assert outcomes._resolve(None) == outcomes.OUTCOMES_PATH


# --------------------------------------------------------------------------
# the boundary this module must never cross
# --------------------------------------------------------------------------

def test_nothing_in_engine_reads_outcomes() -> None:
    """Attribution observes; it never steers. The moment engine/brain.py (or
    any other decision module) imports this, a number derived from a toy
    world starts moving money -- the same tripwire engine/negotiation.py
    carried through Phase 2, for the same reason."""
    engine_dir = Path(outcomes.__file__).resolve().parent
    offenders = [
        path.name for path in sorted(engine_dir.glob("*.py"))
        if path.name != "outcomes.py"
        and "outcomes" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"engine modules must not read outcomes: {offenders}"


def test_the_summary_totals_reconcile_with_the_records() -> None:
    led = ledger(horizon=14)
    act(led, 0, invoice="INV-1")
    act(led, 20, invoice="INV-1")
    pay(led, 1, 500_000, invoice="INV-1")
    pay(led, 40, 300_000, invoice="INV-1")

    result = led.attribute()
    summary = result["summary"]
    assert summary["actions_recorded"] == len(result["records"])
    assert summary["paise_attributed"] == sum(
        r["paise_recovered_within_horizon"] for r in result["records"])
    assert summary["paise_unattributed"] == sum(
        p["amount_paise"] for p in result["unattributed"])
    assert summary["payments_recorded"] == (
        summary["payments_attributed"] + summary["payments_unattributed"])
    assert summary["attribution_horizon_days"] == 14


@pytest.mark.parametrize("action_day", [1, 30])
def test_an_action_after_the_payment_never_claims_it(action_day: int) -> None:
    """The horizon looks backwards from the payment, never forwards."""
    led = ledger(horizon=14)
    pay(led, 0, 400_000)
    act(led, action_day)

    result = led.attribute()
    assert result["records"][0]["paise_recovered_within_horizon"] == 0
    assert result["summary"]["payments_unattributed"] == 1
