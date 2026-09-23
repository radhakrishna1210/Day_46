"""Tests for scripts/compare_grids.py -- the hand-typed vs. fitted comparison.

Reads the REAL config/rules.yaml and config/learned_recovery.yaml (there is
nothing to fake: this script's whole job is reporting on those two files as
they stand), so these tests are also a live check that the current fit still
produces the shape this script depends on.
"""

from __future__ import annotations

from scripts import compare_grids as cg


def test_build_rows_covers_every_quadrant_action_pair_exactly_once() -> None:
    from engine import ability_willingness as aw
    from engine import negotiation as neg

    rows = cg.build_rows()
    keys = [(r["quadrant"], r["action_kind"]) for r in rows]
    assert len(keys) == len(aw.QUADRANTS) * len(neg.ACTIONS)
    assert len(set(keys)) == len(keys), "no (quadrant, action) pair repeated"
    assert set(keys) == {(q, a) for q in aw.QUADRANTS for a in neg.ACTIONS}


def test_structurally_unfitted_actions_never_score() -> None:
    """human_handoff / legal_escalation have no learned cell on ANY quadrant,
    on principle (see the module docstring) -- not an accident of what
    happened to get observed this fit."""
    rows = cg.build_rows()
    for r in rows:
        if r["action_kind"] in cg.STRUCTURALLY_UNFITTED:
            assert r["score"] is None
            assert r["learned_pct"] is None
            assert "structurally unmeasured" in r["note"]


def test_wait_is_fitted_on_every_quadrant_since_phase_e2() -> None:
    """Before E2 wait was structurally unmeasured -- no attributable row, so its
    hand-typed value was never tested. E2 records an EV-chosen wait, so every
    quadrant now has a fitted, well-observed wait cell."""
    from engine import negotiation as neg
    assert neg.WAIT not in cg.STRUCTURALLY_UNFITTED
    waits = [r for r in cg.build_rows() if r["action_kind"] == neg.WAIT]
    assert len(waits) == 4
    for r in waits:
        assert r["learned_pct"] is not None and r["observations"] >= cg.THIN_OBS


def test_featured_cell_is_good_customer_wait_and_is_the_top_score() -> None:
    """The cell the pre-E2 6/6 loss traced back to: hand-typed 60%, never
    tested until E2 -- and, once measured, the single most-wrong cell in the
    grid. In this simulator no payment ever lands behind a wait (money arrives
    only after a message or a kept promise), so the fitted mean sits at the
    Beta(1,1) prior's floor."""
    rows = cg.build_rows()
    featured = next(r for r in rows
                    if (r["quadrant"], r["action_kind"]) == cg.FEATURED_CELL)
    assert featured["quadrant"] == "good_customer" and featured["action_kind"] == "wait"
    assert "FEATURED PRIMARY EXAMPLE" in featured["note"]
    assert featured["hand_typed_pct"] == 60.0
    assert featured["learned_pct"] < 1.0
    assert featured["observations"] >= cg.THIN_OBS

    scored = [r for r in rows if r["score"] is not None]
    assert max(scored, key=lambda r: r["score"]) is featured, (
        "the featured cell should also be the single highest-scoring cell in "
        "the whole grid -- that IS why it is the primary example"
    )


def test_thin_cells_are_excluded_from_the_ranking() -> None:
    rows = cg.build_rows()
    thin = {(r["quadrant"], r["action_kind"]) for r in rows if cg.is_thin(r)}
    assert thin, "fixture: the current fit has thin cells (see docs/learning_data.md)"
    ranked_keys = {(r["quadrant"], r["action_kind"])
                   for r in cg.ranked_most_wrong(rows)}
    assert ranked_keys.isdisjoint(thin)


def test_the_most_wrong_cells_are_the_four_wait_cells_then_good_customer_sends() -> None:
    """Locks in the Phase E2 finding: once measured, every quadrant's wait cell
    is further from its hand-typed value, with more confidence, than any other
    cell. Next come good_customer's soft_nudge and firm -- the hand-typed grid
    was 30-35 points overoptimistic about both. Re-run scripts/fit_recovery.py
    and this may need updating; that is the point of pinning it."""
    ranking = cg.ranked_most_wrong(cg.build_rows())
    top = [(r["quadrant"], r["action_kind"]) for r in ranking[:6]]
    assert top[0] == ("good_customer", "wait")
    assert {q for q, a in top[:4]} == {
        "good_customer", "cash_flow_problem", "can_pay_but_wont", "high_risk"}
    assert all(a == "wait" for _q, a in top[:4])
    assert top[4:6] == [("good_customer", "soft_nudge"), ("good_customer", "firm")]


def test_a_missing_learned_cell_has_no_score_and_is_not_ranked() -> None:
    """high_risk never offers payment_plan, so it is never executed and has no
    learned cell -- distinct from a THIN cell, which has some observations but
    few. It must not silently read as a delta of zero."""
    rows = cg.build_rows()
    row = next(r for r in rows
              if r["quadrant"] == "high_risk" and r["action_kind"] == "payment_plan")
    assert row["score"] is None
    assert row["learned_pct"] is None
    assert "no learned cell" in row["note"]

    ranking = cg.ranked_most_wrong(rows)
    assert ("high_risk", "payment_plan") not in {
        (r["quadrant"], r["action_kind"]) for r in ranking}


def test_main_runs_end_to_end(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["compare_grids.py"])
    assert cg.main() == 0
    out = capsys.readouterr().out
    assert "FEATURED PRIMARY EXAMPLE" in out
    assert "structurally unmeasured" in out
