"""Tests for scripts/fit_recovery.py's fit() -- the re-aggregation, not the sim.

fit() takes outcome rows and produces the per-cell Beta posteriors. The
behaviour that matters:

  * a SEND is grouped by the rung it was DELIVERED at (config ladder tier),
    NOT by proposed_action_kind -- so soft_nudge/firm/legal_facts cells sit
    nested under recovery.<quadrant>.send.<tier>;
  * payment_plan / counter_settle stay one flat cell per quadrant;
  * handoff / right-censored / null-quadrant rows are excluded, and the
    counts are an exact partition of every row seen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from data.generate import SIMULATION_START
from scripts import fit_recovery as fr

DAYS = 120
HORIZON = 14
DAY0 = SIMULATION_START.isoformat()
CENSORED_DAY = (SIMULATION_START + timedelta(days=DAYS - HORIZON + 1)).isoformat()


def row(**over: object) -> dict:
    base = {
        "record_type": "action",
        "quadrant": "good_customer",
        "action_kind": "send",
        "rung": 2,
        "day": DAY0,
        "paid_within_horizon": False,
    }
    base.update(over)
    return base


def _fit(rows: list[dict]) -> dict:
    return fr.fit(rows, DAYS, HORIZON)


# --------------------------------------------------------------------------
# SEND grouping by delivered rung
# --------------------------------------------------------------------------

def test_send_rows_are_grouped_by_delivered_rung_into_ladder_tiers() -> None:
    rows = (
        [row(rung=1, paid_within_horizon=True)] * 3 + [row(rung=1)] * 1
        + [row(rung=2, paid_within_horizon=True)] * 5 + [row(rung=2)] * 5
        + [row(rung=3, paid_within_horizon=True)] * 2 + [row(rung=3)] * 8
    )
    rec = _fit(rows)["recovery"]["good_customer"]["send"]

    assert set(rec) == {"soft_nudge", "firm", "legal_facts"}
    assert rec["soft_nudge"]["delivered_rung"] == 1
    assert rec["firm"]["delivered_rung"] == 2
    assert rec["legal_facts"]["delivered_rung"] == 3

    assert (rec["soft_nudge"]["successes"], rec["soft_nudge"]["failures"]) == (3, 1)
    assert (rec["firm"]["successes"], rec["firm"]["failures"]) == (5, 5)
    assert (rec["legal_facts"]["successes"], rec["legal_facts"]["failures"]) == (2, 8)


def test_no_flat_send_cell_is_produced_for_on_ladder_rungs() -> None:
    rec = _fit([row(rung=2)] * 10)["recovery"]["good_customer"]
    assert "send" in rec and "firm" in rec["send"]
    assert not isinstance(rec["send"].get("alpha"), int)  # send is a group, not a cell


def test_the_tier_names_come_from_the_config_ladder_not_a_hardcode() -> None:
    from engine import rungs
    ladder = {e["id"]: e["name"] for e in rungs.all_rungs() if e["id"] in (1, 2, 3)}
    rows = [row(rung=r) for r in (1, 2, 3)]
    rec = _fit(rows)["recovery"]["good_customer"]["send"]
    assert set(rec) == set(ladder.values())


def test_a_send_at_a_non_buyer_facing_rung_is_kept_in_a_coarse_bucket() -> None:
    fitted = _fit([row(rung=0)] * 4 + [row(rung=2)] * 4)
    assert fitted["counts"]["send_rows_off_ladder"] == 4
    rec = fitted["recovery"]["good_customer"]
    # a flat cell, separate from the per-tier "send" group
    assert rec["send_off_ladder"]["observations"] == 4
    assert "firm" in rec["send"] and "delivered_rung" not in rec.get("send_off_ladder", {})


# --------------------------------------------------------------------------
# payment_plan / counter_settle stay flat
# --------------------------------------------------------------------------

def test_payment_plan_and_counter_settle_are_one_flat_cell_each() -> None:
    rows = (
        [row(action_kind="payment_plan", rung=2, paid_within_horizon=True)] * 6
        + [row(action_kind="payment_plan", rung=3)] * 4
        + [row(action_kind="counter_settle", rung=2)] * 3
    )
    rec = _fit(rows)["recovery"]["good_customer"]
    assert rec["payment_plan"]["successes"] == 6
    assert rec["payment_plan"]["failures"] == 4          # rung is ignored for these
    assert rec["payment_plan"]["observations"] == 10
    assert "delivered_rung" not in rec["payment_plan"]
    assert rec["counter_settle"]["observations"] == 3


# --------------------------------------------------------------------------
# exclusions and the partition
# --------------------------------------------------------------------------

def test_handoff_rows_are_excluded() -> None:
    fitted = _fit([row(action_kind="handoff", rung=4)] * 7 + [row(rung=2)] * 3)
    assert fitted["counts"]["excluded_handoff"] == 7
    assert fitted["counts"]["fitted_observations"] == 3
    assert "handoff" not in fitted["recovery"].get("good_customer", {})


def test_right_censored_rows_are_excluded() -> None:
    fitted = _fit([row(rung=2, day=CENSORED_DAY)] * 5 + [row(rung=2)] * 4)
    assert fitted["counts"]["excluded_right_censored"] == 5
    assert fitted["counts"]["fitted_observations"] == 4


def test_null_quadrant_rows_are_excluded() -> None:
    fitted = _fit([row(rung=2, quadrant=None)] * 2 + [row(rung=2)] * 6)
    assert fitted["counts"]["excluded_null_quadrant"] == 2
    assert fitted["counts"]["fitted_observations"] == 6


def test_non_action_records_are_ignored() -> None:
    rows = [row(rung=2)] * 3 + [{"record_type": "unattributed_payment", "amount_paise": 1}]
    fitted = _fit(rows)
    assert fitted["counts"]["action_rows_seen"] == 3


def test_counts_are_an_exact_partition() -> None:
    rows = (
        [row(rung=1)] * 4 + [row(rung=2)] * 10 + [row(rung=3)] * 6
        + [row(action_kind="payment_plan", rung=2)] * 5
        + [row(action_kind="handoff", rung=4)] * 8
        + [row(rung=2, day=CENSORED_DAY)] * 3
        + [row(rung=2, quadrant=None)] * 2
    )
    c = _fit(rows)["counts"]
    assert c["action_rows_seen"] == 38
    assert (c["excluded_handoff"] + c["excluded_right_censored"]
            + c["excluded_null_quadrant"] + c["fitted_observations"]) == c["action_rows_seen"]
    assert c["send_rows_off_ladder"] == 0


# --------------------------------------------------------------------------
# the Beta fit itself
# --------------------------------------------------------------------------

def test_the_cell_is_beta_1_plus_successes_1_plus_failures() -> None:
    cell = fr._fit_cell(successes=7, failures=13)
    assert cell["alpha"] == 8
    assert cell["beta"] == 14
    assert cell["mean"] == pytest.approx(8 / 22, abs=1e-4)
    assert cell["observations"] == 20


def test_a_thin_cell_has_a_visibly_wider_credible_interval() -> None:
    thin = fr._fit_cell(successes=1, failures=8)      # n = 9
    fat = fr._fit_cell(successes=110, failures=548)   # n ~ 658
    assert thin["ci95_width"] > 3 * fat["ci95_width"]
    assert thin["observations"] < fr.THIN_OBS <= fat["observations"]
