"""Read-only inspection of audit/outcomes.jsonl -- the raw attribution picture.

ANALYSIS ONLY. This script imports engine.outcomes to read the file and
data.generate for the simulation start date; it changes nothing, writes
nothing, and fixes nothing. It exists to answer "what is actually in there?"
before anyone tries to fit anything to it.

    python scripts/inspect_outcomes.py
    python scripts/inspect_outcomes.py --path audit/outcomes.jsonl --days 120

What it prints, in order:

  1. RUN INVENTORY -- engine.outcomes.runs(), so a file that silently stacked
     two invocations is visible on the first screen rather than after someone
     has already summed it (see engine/outcomes.py's FILE LIFECYCLE note).
  2. TOTALS -- rows, action rows, unattributed rows, and how many action rows
     carry quadrant == null.
  3. QUADRANT x ACTION_KIND counts.
  4. QUADRANT x ACTION_KIND raw success rate (paid_within_horizon).
  5. UNATTRIBUTED payments -- count and total paise.
  6. RIGHT-CENSORED actions -- those whose attribution horizon runs past the
     end of the simulated window, and so cannot have been fully observed.

Tables are printed per mode (baseline / agent / agent_ev) and then combined.
Per mode first on purpose: the baseline arm has no two-axis score at all, so
every one of its rows is quadrant null, and folding it into the agent arms'
nulls would merge two different meanings of "null" into one cell.

RIGHT-CENSORING, and why the run end is computed rather than read:
outcomes.jsonl rows carry the day an action happened but not the window it
happened inside. The window is derivable and fixed -- data.generate's
SIMULATION_START (a module constant, seed-independent) plus --days -- so that
is what is used, and the value is printed so it can be checked. An action on
day D is fully observed only if D + horizon <= run_end; otherwise its verdict
was taken against a horizon the run ended inside, and can only look worse
than the truth. Those are the rows to exclude before fitting anything. The
latest row day actually present is printed next to the computed run end as a
cross check, and a row past that end is called out loudly.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data.generate import SIMULATION_START
from engine import outcomes
from engine.money import enable_unicode_output

#: Print order for the three arms. Anything unrecognised sorts after them.
MODE_ORDER = ("baseline", "agent", "agent_ev")

#: How a null quadrant is spelled in a table cell. Never blank -- a blank
#: cell reads as "no rows", and null-quadrant rows are the opposite of that.
NULL = "(null)"

TOTAL = "TOTAL"


def _mode_key(mode: str) -> tuple[int, str]:
    return (MODE_ORDER.index(mode) if mode in MODE_ORDER else len(MODE_ORDER), mode)


def _modes(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({row["mode"] for row in rows}, key=_mode_key)


def _quadrant(row: dict[str, Any]) -> str:
    return NULL if row.get("quadrant") is None else row["quadrant"]


def _grid(actions: list[dict[str, Any]],
          cell: Callable[[list[dict[str, Any]]], str],
          title: str, width: int) -> None:
    """One quadrant x action_kind table, with TOTAL margins on both axes.

    The margins are recomputed from `actions` rather than summed out of the
    cells, so a cell function that is not additive (a rate is not) still gets
    a correct total instead of an average of averages.
    """
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in actions:
        buckets[(_quadrant(row), row["action_kind"])].append(row)
    # Nulls last: they are the absence of a quadrant, not a fifth one.
    quadrants = sorted({q for q, _ in buckets}, key=lambda q: (q == NULL, q))
    kinds = sorted({k for _, k in buckets})

    def row_for(label: str, subset: list[dict[str, Any]],
                pick: Callable[[str], list[dict[str, Any]]]) -> str:
        line = f"    {label:<20}"
        for kind in kinds:
            line += f"{cell(pick(kind)):>{width}}"
        return line + f"{cell(subset):>{width}}"

    header = (f"    {'quadrant':<20}" + "".join(f"{k:>{width}}" for k in kinds)
              + f"{TOTAL:>{width}}")
    print(f"  {title}")
    print(header)
    print("    " + "-" * (len(header) - 4))
    for quadrant in quadrants:
        subset = [r for r in actions if _quadrant(r) == quadrant]
        print(row_for(quadrant, subset, lambda k, q=quadrant: buckets.get((q, k), [])))
    print("    " + "-" * (len(header) - 4))
    print(row_for(TOTAL, actions,
                  lambda k: [r for r in actions if r["action_kind"] == k]))


def _count_cell(rows: list[dict[str, Any]]) -> str:
    return str(len(rows)) if rows else "-"


def _rate_cell(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "-"
    hits = sum(1 for r in rows if r["paid_within_horizon"])
    return f"{hits / len(rows):.2f} {hits}/{len(rows)}"


def _print_inventory(path: Path) -> None:
    found = outcomes.runs(path)
    print(f"RUN INVENTORY -- {len(found)} run(s) in {path}")
    print(f"  {'run_id':<42}{'mode':<11}{'seed':>6}{'actions':>10}{'unattrib':>10}")
    for entry in sorted(found.values(), key=lambda e: (e["seed"], _mode_key(e["mode"]))):
        print(f"  {entry['run_id']:<42}{entry['mode']:<11}{entry['seed']:>6}"
              f"{entry['action_rows']:>10}{entry['unattributed_rows']:>10}")
    print(f"  seeds present: {sorted({e['seed'] for e in found.values()})}")


def _print_totals(rows: list[dict[str, Any]], actions: list[dict[str, Any]],
                  unattributed: list[dict[str, Any]]) -> None:
    null_rows = [r for r in actions if r.get("quadrant") is None]
    share = f"{len(null_rows) / len(actions):.1%}" if actions else "n/a"
    print("TOTALS")
    print(f"  total records              {len(rows):>8}")
    print(f"  action rows                {len(actions):>8}")
    print(f"  unattributed payment rows  {len(unattributed):>8}")
    print(f"  action rows quadrant==null {len(null_rows):>8}   ({share} of action rows)")
    by_mode: dict[str, int] = defaultdict(int)
    for row in null_rows:
        by_mode[row["mode"]] += 1
    if by_mode:
        print("    by mode: " + ", ".join(
            f"{m}={by_mode[m]}" for m in sorted(by_mode, key=_mode_key)))


def _print_unattributed(unattributed: list[dict[str, Any]]) -> None:
    total = sum(row["amount_paise"] for row in unattributed)
    print("UNATTRIBUTED PAYMENTS -- money that landed with no action inside its horizon")
    print(f"  count {len(unattributed):>7}    total {total:>16,} paise  "
          f"(Rs {total / 100:,.2f})")
    by_mode: dict[str, list[int]] = defaultdict(list)
    for row in unattributed:
        by_mode[row["mode"]].append(row["amount_paise"])
    for mode in sorted(by_mode, key=_mode_key):
        amounts = by_mode[mode]
        print(f"    {mode:<11}{len(amounts):>6} payments {sum(amounts):>16,} paise")


def _print_censoring(actions: list[dict[str, Any]], horizon: int,
                     run_end: date, days: int) -> None:
    print("RIGHT-CENSORED ACTIONS -- horizon extends past the end of the run")
    print(f"  window: {SIMULATION_START.isoformat()} + {days} days -> run_end "
          f"{run_end.isoformat()};  horizon {horizon}d")
    if not actions:
        print("  no action rows")
        return
    latest = max(date.fromisoformat(r["day"]) for r in actions)
    print(f"  latest action day present in the file: {latest.isoformat()}")
    if latest > run_end:
        print("  !! rows exist AFTER the computed run_end -- the --days assumption is "
              "wrong for this file, and the counts below cannot be trusted")
    cutoff = run_end - timedelta(days=horizon)
    censored = [r for r in actions if date.fromisoformat(r["day"]) > cutoff]
    print(f"  fully observed <=> action day <= {cutoff.isoformat()}")
    print(f"  censored {len(censored)} of {len(actions)} action rows "
          f"({len(censored) / len(actions):.1%}) -- exclude these before fitting")
    by_mode: dict[str, int] = defaultdict(int)
    for row in censored:
        by_mode[row["mode"]] += 1
    for mode in sorted(by_mode, key=_mode_key):
        total = sum(1 for r in actions if r["mode"] == mode)
        print(f"    {mode:<11}{by_mode[mode]:>6} of {total:>6}  "
              f"({by_mode[mode] / total:.1%})")
    if censored:
        hits = sum(1 for r in censored if r["paid_within_horizon"])
        print(f"  of those, {hits} already show paid_within_horizon -- those verdicts "
              f"stand; it is the {len(censored) - hits} apparent failures that may not.")


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(
        description="Inspect audit/outcomes.jsonl. Read-only; changes nothing.")
    parser.add_argument("--path", type=Path, default=outcomes.OUTCOMES_PATH,
                        help="outcomes.jsonl to read (default: audit/outcomes.jsonl)")
    parser.add_argument("--days", type=int, default=120,
                        help="simulated days the run used, for the right-censoring "
                             "cutoff (default: 120, sim/run_sim.py's DEFAULT_DAYS)")
    parser.add_argument("--horizon", type=int, default=None,
                        help="attribution horizon in days (default: config "
                             "learning.attribution_horizon_days)")
    args = parser.parse_args()

    rows = outcomes.records(args.path)
    if not rows:
        print(f"no records in {args.path} -- run sim/run_sim.py first")
        return 1

    horizon = args.horizon if args.horizon is not None else outcomes.horizon_days()
    run_end = SIMULATION_START + timedelta(days=args.days - 1)
    actions = [r for r in rows if r["record_type"] == outcomes.ACTION_RECORD]
    unattributed = [r for r in rows
                    if r["record_type"] == outcomes.UNATTRIBUTED_PAYMENT_RECORD]

    _print_inventory(args.path)
    print()
    _print_totals(rows, actions, unattributed)

    modes = _modes(actions)
    for mode in modes:
        subset = [r for r in actions if r["mode"] == mode]
        print()
        print(f"MODE: {mode}  ({len(subset)} action rows)")
        _grid(subset, _count_cell, "counts", width=14)
        print()
        _grid(subset, _rate_cell, "raw success rate -- paid_within_horizon "
                                  "(rate hits/n)", width=16)

    if len(modes) > 1:
        print()
        print(f"ALL MODES COMBINED  ({len(actions)} action rows)")
        _grid(actions, _count_cell, "counts", width=14)
        print()
        _grid(actions, _rate_cell, "raw success rate -- paid_within_horizon "
                                   "(rate hits/n)", width=16)

    print()
    _print_unattributed(unattributed)
    print()
    _print_censoring(actions, horizon, run_end, args.days)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
