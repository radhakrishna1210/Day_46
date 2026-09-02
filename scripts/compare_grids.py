"""Hand-typed vs. fitted recovery-probability grids -- where they disagree,
and by how much that disagreement should be trusted.

READ ONLY. Reads config/rules.yaml's hand-typed negotiation.recovery_probability
grid and config/learned_recovery.yaml's fitted posteriors (scripts/fit_recovery.py)
through engine/config.py's cached loaders -- the same one place every other
module reads config from -- and reports, per (quadrant, action) cell:

    hand-typed value, fitted posterior mean, the delta in percentage points,
    and a MOST-WRONG SCORE = |delta_points| * (observations / ci95_width)

The score is deliberately not just |delta|: a big swing on a thin, wide-CI
cell (the rung-1 SEND cells docs/learning_findings.md's "Thin SEND cells at
rung 1" section already flags -- n=9-79) is noise dressed as a finding, while
a smaller swing backed by hundreds of observations and a tight interval is the
one worth paying attention to. Weighting by observations/ci95_width makes a
well-fitted, large delta rank above a big-but-shaky one without hiding either.

Nothing here writes a file or changes a config value. docs/learning_findings.md
is hand-authored (see its own header), not generated -- this script is the
analysis a reader can re-run to check a claim made there, the same relationship
scripts/fit_recovery.py has to docs/learning_data.md's generated provenance.

Three actions never get a learned cell, structurally, not as a data gap this
fit could close with more seeds: `wait` produces no attributable action row
(nothing is sent, so engine/outcomes.py has nothing to credit or fail), and
`human_handoff` / `legal_escalation` both execute as a rung-4 `handoff` whose
post-handoff recovery this simulator has no model of (scripts/fit_recovery.py's
own EXCLUDED_ACTION_KINDS). Their hand-typed values are printed for reference
but never scored or ranked -- there is no fitted number to compare them against,
and there structurally never will be under the current simulator.

    python scripts/compare_grids.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import ability_willingness as aw
from engine import negotiation as neg
from engine.config import learned_recovery, rules

#: Mirrors scripts/fit_recovery.py's own THIN_OBS -- a cell below this many
#: observations is thin regardless of what its raw delta says.
THIN_OBS = 100

#: Actions with NO possible learned cell, structurally -- see the module
#: docstring. Printed for reference, excluded from scoring and ranking.
STRUCTURALLY_UNFITTED: frozenset[str] = frozenset({
    neg.WAIT, neg.HUMAN_HANDOFF, neg.LEGAL_ESCALATION,
})

#: (quadrant, action) cells docs/learning_findings.md's "Thin SEND cells at
#: rung 1" section already names and explains (n=9-79, ci95_width 0.21-0.42).
#: Excluded from the "most-wrong" ranking below -- restating a delta on a cell
#: that section already covers would not be a new finding.
ALREADY_FLAGGED_THIN: frozenset[tuple[str, str]] = frozenset({
    ("good_customer", neg.SOFT_NUDGE),
    ("cash_flow_problem", neg.SOFT_NUDGE),
    ("high_risk", neg.SOFT_NUDGE),
})

#: docs/learning_findings.md's headline example: the single best-fitted cell
#: in the file (n=748, the file's narrowest ci95_width) also carries the
#: largest score of any cell -- see that file for the full story of what this
#: delta explains.
FEATURED_CELL: tuple[str, str] = ("good_customer", neg.FIRM)


def _flatten_learned(recovery: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """{(quadrant, action_kind): cell} for every fitted cell -- SEND tiers keyed
    by their own tier name (soft_nudge/firm/legal_facts), which IS the
    negotiation action name (engine/learning.py's _resolve_cell does the same
    identity mapping), so this needs no separate rung lookup."""
    flat: dict[tuple[str, str], dict[str, Any]] = {}
    for quadrant, cells in recovery.items():
        for tier, cell in (cells.get("send") or {}).items():
            flat[(quadrant, tier)] = cell
        for action_kind, cell in cells.items():
            if action_kind != "send":
                flat[(quadrant, action_kind)] = cell
    return flat


def build_rows() -> list[dict[str, Any]]:
    """One row per (quadrant, action) in the hand-typed grid -- every cell that
    could possibly exist, whether or not a learned number backs it."""
    grid = rules()["negotiation"]["recovery_probability"]
    learned = _flatten_learned(learned_recovery().get("recovery", {}))

    rows = []
    for quadrant in aw.QUADRANTS:
        for action_kind in neg.ACTIONS:
            hand_typed_pct = float(grid[quadrant][action_kind])
            cell = learned.get((quadrant, action_kind))
            row: dict[str, Any] = {
                "quadrant": quadrant, "action_kind": action_kind,
                "hand_typed_pct": hand_typed_pct,
                "learned_pct": None, "delta_pts": None,
                "observations": None, "ci95_width": None, "score": None,
                "note": "",
            }
            if action_kind in STRUCTURALLY_UNFITTED:
                row["note"] = "structurally unmeasured -- never executed as its own attributable action"
            elif cell is None:
                row["note"] = "no learned cell (never recorded in training) -- falls back to hand-typed"
            else:
                learned_pct = float(cell["mean"]) * 100
                obs = int(cell["observations"])
                ci = float(cell["ci95_width"])
                delta = learned_pct - hand_typed_pct
                row.update({
                    "learned_pct": learned_pct, "delta_pts": delta,
                    "observations": obs, "ci95_width": ci,
                    "score": abs(delta) * (obs / ci if ci else 0.0),
                })
                if obs < THIN_OBS:
                    row["note"] = f"thin (n<{THIN_OBS})"
                if (quadrant, action_kind) in ALREADY_FLAGGED_THIN:
                    row["note"] = "thin -- already flagged, docs/learning_findings.md"
                if (quadrant, action_kind) == FEATURED_CELL:
                    row["note"] = (row["note"] + "; " if row["note"] else "") + "FEATURED PRIMARY EXAMPLE"
            rows.append(row)
    return rows


def ranked_most_wrong(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every scoreable cell (a learned number exists), best-fitted-and-most-
    wrong first, EXCLUDING the already-flagged thin cells -- those have their
    own explanation on file and restating them here would not be a new one."""
    candidates = [r for r in rows if r["score"] is not None
                 and (r["quadrant"], r["action_kind"]) not in ALREADY_FLAGGED_THIN]
    return sorted(candidates, key=lambda r: -r["score"])


def _fmt_pct(value: float | None) -> str:
    return f"{value:5.1f}" if value is not None else "  n/a"


def _fmt_delta(value: float | None) -> str:
    return f"{value:+6.1f}" if value is not None else "   n/a"


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'quadrant':<18} {'action':<15} {'hand-typed':>10} {'learned':>8} "
          f"{'delta':>8} {'n':>5} {'ci95':>7} {'score':>10}  note")
    for r in rows:
        n = f"{r['observations']:5d}" if r["observations"] is not None else "  n/a"
        ci = f"{r['ci95_width']:7.4f}" if r["ci95_width"] is not None else "    n/a"
        score = f"{r['score']:10.1f}" if r["score"] is not None else "       n/a"
        print(f"{r['quadrant']:<18} {r['action_kind']:<15} {_fmt_pct(r['hand_typed_pct']):>10}%"
              f" {_fmt_pct(r['learned_pct']):>7}% {_fmt_delta(r['delta_pts'])}pt {n} {ci} {score}"
              f"  {r['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare config/rules.yaml's hand-typed recovery_probability grid "
                    "against config/learned_recovery.yaml's fitted posteriors. Read-only.")
    parser.add_argument("--top", type=int, default=3,
                        help="how many of the most-wrong (non-thin) cells to call out "
                             "at the end (default: 3, including the featured cell)")
    args = parser.parse_args()

    rows = build_rows()
    print("Every (quadrant, action) cell -- hand-typed grid vs. fitted posterior:\n")
    print_table(rows)

    ranking = ranked_most_wrong(rows)
    print(f"\nMost-wrong cells, ranked by |delta| x (observations / ci95_width), "
          f"excluding cells docs/learning_findings.md already flags as thin:\n")
    for i, r in enumerate(ranking[:args.top], start=1):
        featured = " <-- FEATURED PRIMARY EXAMPLE" if (r["quadrant"], r["action_kind"]) == FEATURED_CELL else ""
        print(f"  #{i}  {r['quadrant']:<18} {r['action_kind']:<15} "
              f"hand-typed={r['hand_typed_pct']:.1f}%  learned={r['learned_pct']:.2f}%  "
              f"delta={r['delta_pts']:+.1f}pt  n={r['observations']}  "
              f"ci95_width={r['ci95_width']:.4f}  score={r['score']:.1f}{featured}")

    print(
        "\nwait is structurally unmeasured, on every quadrant: it produces no "
        "attributable action row (nothing is sent, so engine/outcomes.py has "
        "nothing to credit or fail against), so its hand-typed value never gets "
        "checked against real outcomes and cannot be fit with more training data "
        "under the current simulator. When a learned cell sits close to wait's "
        "own untouched number (as good_customer/firm's 61.47% does to wait's "
        "60%), the comparison is between one number the data corrected and one "
        "that was never tested at all -- see docs/learning_findings.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
