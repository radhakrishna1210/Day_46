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
    """wait / human_handoff / legal_escalation have no learned cell on ANY
    quadrant, on principle (see the module docstring) -- not an accident of
    what happened to get observed this fit."""
    rows = cg.build_rows()
    for r in rows:
        if r["action_kind"] in cg.STRUCTURALLY_UNFITTED:
            assert r["score"] is None
            assert r["learned_pct"] is None
            assert "structurally unmeasured" in r["note"]


def test_featured_cell_is_good_customer_firm_and_is_the_top_score() -> None:
    rows = cg.build_rows()
    featured = next(r for r in rows
                    if (r["quadrant"], r["action_kind"]) == cg.FEATURED_CELL)
    assert featured["quadrant"] == "good_customer" and featured["action_kind"] == "firm"
    assert "FEATURED PRIMARY EXAMPLE" in featured["note"]
    assert featured["hand_typed_pct"] == 88.0
    # n=748, the file's tightest ci95_width -- the best-fitted cell there is.
    assert featured["observations"] == 748
    assert featured["ci95_width"] < 0.08

    scored = [r for r in rows if r["score"] is not None]
    assert max(scored, key=lambda r: r["score"]) is featured, (
        "the featured cell should also be the single highest-scoring cell in "
        "the whole grid -- that IS why it is the primary example"
    )


def test_already_flagged_thin_cells_are_excluded_from_the_ranking() -> None:
    ranking = cg.ranked_most_wrong(cg.build_rows())
    ranked_keys = {(r["quadrant"], r["action_kind"]) for r in ranking}
    assert ranked_keys.isdisjoint(cg.ALREADY_FLAGGED_THIN)


def test_the_other_two_most_wrong_cells_after_the_featured_one() -> None:
    """Locks in the Day 9 finding: after good_customer/firm (the featured
    cell) and the three already-flagged thin soft_nudge cells, the next two
    most-wrong cells by |delta| x (observations / ci95_width) are
    good_customer/payment_plan and can_pay_but_wont/firm -- both well-fitted
    (n>400, ci95_width<0.1), not thin. Re-run scripts/fit_recovery.py and this
    may need updating; that is the point of pinning it."""
    ranking = cg.ranked_most_wrong(cg.build_rows())
    top_three = [(r["quadrant"], r["action_kind"]) for r in ranking[:3]]
    assert top_three == [
        ("good_customer", "firm"),
        ("good_customer", "payment_plan"),
        ("can_pay_but_wont", "firm"),
    ]


def test_a_missing_learned_cell_has_no_score_and_is_not_ranked() -> None:
    """can_pay_but_wont has no rung-1 (soft_nudge) sends in training at all
    (docs/learning_findings.md) -- distinct from a THIN cell, which has some
    observations but few. This one has none, and must not silently read as a
    delta of zero."""
    rows = cg.build_rows()
    row = next(r for r in rows
              if r["quadrant"] == "can_pay_but_wont" and r["action_kind"] == "soft_nudge")
    assert row["score"] is None
    assert row["learned_pct"] is None
    assert "no learned cell" in row["note"]

    ranking = cg.ranked_most_wrong(rows)
    assert ("can_pay_but_wont", "soft_nudge") not in {
        (r["quadrant"], r["action_kind"]) for r in ranking}


def test_main_runs_end_to_end(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["compare_grids.py"])
    assert cg.main() == 0
    out = capsys.readouterr().out
    assert "FEATURED PRIMARY EXAMPLE" in out
    assert "structurally unmeasured" in out
