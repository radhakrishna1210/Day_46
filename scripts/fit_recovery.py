"""Fit recovery-probability posteriors from exploration-mode simulator runs.

TRAINING ONLY, and OUTPUT ONLY. This script runs sim/run_sim.py's exploration
mode (run_agent(explore=True)) across a fixed block of TRAINING seeds, reads
back the outcome ledger it produced, and fits one Beta posterior per cell. It
writes two files and changes nothing else:

    config/learned_recovery.yaml   the posteriors
    docs/learning_data.md          the provenance record -- what was trained on

engine/learning.py reads config/learned_recovery.yaml (behind config/rules.yaml's
learning.enabled switch, which SHIPS OFF). Analysis notes and known limitations
live in docs/learning_findings.md, which is hand-authored, not generated here.

CELL GRANULARITY -- read this before trusting a SEND cell:

  * payment_plan and counter_settle map 1:1 from what EV selected to what was
    executed, so each gets ONE cell per quadrant, keyed by its own name.
  * a SEND is different. engine/negotiation.py's action space distinguishes
    soft_nudge / firm / legal_facts, but engine/brain.py's escalation walk
    decides the actually-delivered rung INDEPENDENTLY of which of those three
    EV selected -- proposed_action_kind matched the delivered rung only 7-80%
    of the time in this training set, and gate_override was true on 56% of
    SEND rows. So a SEND is grouped by the rung it was DELIVERED at, mapped to
    a tier name through config/rules.yaml's own ladder (rung 1 = soft_nudge,
    2 = firm, 3 = legal_facts -- not a new mapping). Those cells live nested
    under recovery.<quadrant>.send.<tier>. See docs/learning_findings.md for
    the full label/execution-gap write-up.

TRAINING vs BENCHMARK, the one rule that matters here:

  * TRAINING seeds are 1000-1029 (30 seeds). Only these are used for fitting.
  * BENCHMARK seeds are 42, 7, 13, 99, 2024, 555 -- the seeds every headline
    number in results.json is measured on. They are HELD OUT. Fitting on a
    benchmark seed would let the learned numbers quietly memorise the very
    world they are later evaluated against, so the two sets are asserted
    disjoint before a single run starts.

THE FIT, exactly as specified:

  * For each cell, count successes (an action row with paid_within_horizon
    true) and failures (false), then
        alpha = 1 + successes,  beta = 1 + failures
    -- a weak uniform Beta(1, 1) prior, so a cell with no data sits at
    mean 0.5 with a credible interval nearly the full width of [0, 1] and is
    visibly, honestly uncertain rather than silently absent.
  * mean       = alpha / (alpha + beta)  (the posterior mean)
  * ci95_width = width of the 95% central credible interval of Beta(alpha,
    beta). Wide == thin cell == do not trust the mean yet. The rung-1
    (soft_nudge) SEND cells are thin (n = 9-79) because the escalation walk
    rarely stops at rung 1; their ci95_width is what says so.

WHAT IS EXCLUDED BEFORE FITTING, and why:

  * RIGHT-CENSORED actions -- an action taken so late in the simulated window
    that its full attribution horizon runs past the end of the run. Its
    verdict was taken against a horizon the run ended inside, so it can only
    ever look worse than the truth. Cutoff is computed from
    data.generate.SIMULATION_START + --days (seed-independent), matching
    scripts/inspect_outcomes.py.
  * HANDOFF action rows -- the simulator has no model of what the MSME owner
    does after taking a case over, so a handoff's recovery is structurally 0%
    in every run (see sim/run_sim.py's own comment, and CLAUDE.md's note:
    "anything that later learns from this file has to exclude them rather than
    read them as failures"). Excluded, not fitted as a dead cell.
  * NULL-quadrant action rows -- exploration mode always carries a quadrant,
    so this is belt-and-braces; any that appear are counted, never guessed at.

REPRODUCIBILITY: every exploration roll is seeded from (seed, invoice, day)
(sim/run_sim.py), so the fitted numbers are identical run to run. The
outcomes JSONL itself is not byte-reproducible -- its run_id carries a wall
clock -- but nothing downstream reads that.

    python scripts/fit_recovery.py
    python scripts/fit_recovery.py --days 120 --outcomes-path audit/outcomes_train.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from scipy.stats import beta as beta_dist

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import generate, store
from data.generate import SIMULATION_START
from engine import audit, outcomes, rungs
from engine.money import enable_unicode_output

ROOT = Path(__file__).resolve().parents[1]


class _FlowList(list):
    """A list yaml.safe_dump renders inline -- for the seed lists, which are
    far more readable as ``[1000, 1001, ...]`` than 30 block entries."""


yaml.SafeDumper.add_representer(
    _FlowList,
    lambda dumper, data: dumper.represent_sequence(
        "tag:yaml.org,2002:seq", data, flow_style=True),
)


def _rel(path: Path) -> str:
    """`path` relative to the repo root when it lives under it, else as-is."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


#: The training block. 30 seeds, fixed, arbitrary -- picked once and never
#: tuned to make a cell look better, the same discipline sim/run_sim.py's
#: DEFAULT_EXTRA_SEEDS were picked under.
TRAINING_SEEDS: tuple[int, ...] = tuple(range(1000, 1030))

#: Every seed results.json is measured on (sim/run_sim.py's --seed default
#: plus DEFAULT_EXTRA_SEEDS). NEVER fit on these.
BENCHMARK_SEEDS: frozenset[int] = frozenset({42, 7, 13, 99, 2024, 555})

#: Recorded action kinds whose outcome the simulator cannot actually observe.
#: "handoff" is engine.brain.HANDOFF -- the executed kind for both
#: human_handoff and legal_escalation. See the module docstring.
EXCLUDED_ACTION_KINDS: frozenset[str] = frozenset({"handoff"})

DEFAULT_DAYS = 120
DEFAULT_OUTCOMES_PATH = ROOT / "audit" / "outcomes_train.jsonl"
DEFAULT_YAML_OUT = ROOT / "config" / "learned_recovery.yaml"
DEFAULT_DOC_OUT = ROOT / "docs" / "learning_data.md"

#: The executed action_kind for a buyer-facing message. Grouped by DELIVERED
#: rung (see the module docstring), never by proposed_action_kind.
SEND = "send"

#: Below this many observations, a cell's point estimate is not worth trusting
#: -- flagged in docs/learning_data.md. The ci95_width carries the quantitative
#: version of the same warning.
THIN_OBS = 100


# --------------------------------------------------------------------------
# running the training simulations
# --------------------------------------------------------------------------

def _run_training(seeds: tuple[int, ...], days: int, outcomes_path: Path) -> None:
    """Run exploration mode once per training seed, accumulating into one file.

    run_agent() clears and rewrites the shared audit trail and regenerates
    data/seed/ on every call, so this snapshots both and puts them back --
    the same courtesy sim/run_sim.py's multi_seed_summary() extends to the
    primary seed.
    """
    # Imported here, not at module top: importing sim.run_sim has a cost
    # (it pulls in the whole engine) and a --help should not pay it.
    from sim.run_sim import run_agent

    original_seed = _original_seed()
    trail = audit.snapshot()

    previous_path = outcomes.OUTCOMES_PATH
    outcomes.OUTCOMES_PATH = outcomes_path
    try:
        outcomes.start_file()
        for i, seed in enumerate(seeds, start=1):
            print(f"  [{i:>2}/{len(seeds)}] seed {seed} ... ", end="", flush=True)
            report = run_agent(seed, days, explore=True)
            print(f"{report['outcomes']['actions_recorded']} action rows")
    finally:
        outcomes.OUTCOMES_PATH = previous_path
        generate.ensure_dataset(original_seed)
        audit.restore(trail)


def _original_seed() -> int:
    """The seed data/seed/ held before this script touched it, so it can be
    put back. Falls back to sim/run_sim.py's own default."""
    try:
        return int(store.load_meta()["seed"])
    except Exception:
        return 42


# --------------------------------------------------------------------------
# fitting
# --------------------------------------------------------------------------

def _run_end(days: int) -> date:
    """Last simulated day. Seed-independent -- see scripts/inspect_outcomes.py."""
    return SIMULATION_START + timedelta(days=days - 1)


def _tier_by_rung() -> dict[int, str]:
    """rung id -> ladder tier name, for the buyer-facing rungs only.

    config/rules.yaml's ladder is the single source (via engine.rungs): rung 1
    is "soft_nudge", 2 "firm", 3 "legal_facts". This script never restates that
    mapping -- if a rung is renamed in config, the cells follow.
    """
    return {entry["id"]: entry["name"] for entry in rungs.all_rungs()
            if entry["id"] in rungs.BUYER_FACING_RUNGS}


def _fit_cell(successes: int, failures: int, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Beta(1 + successes, 1 + failures) -- posterior mean and 95% CI width.

    `extra` (e.g. ``{"delivered_rung": 2}``) is merged in front, so provenance
    keys read first in the YAML.
    """
    alpha = 1 + successes
    beta = 1 + failures
    lo, hi = beta_dist.ppf([0.025, 0.975], alpha, beta)
    return {
        **(extra or {}),
        "alpha": alpha,
        "beta": beta,
        "mean": round(alpha / (alpha + beta), 4),
        "ci95_width": round(float(hi - lo), 4),
        "observations": successes + failures,
        "successes": successes,
        "failures": failures,
    }


def fit(rows: list[dict[str, Any]], days: int, horizon: int) -> dict[str, Any]:
    """Partition the action rows and fit one Beta cell per group.

    Grouping:
      * a SEND row -> (quadrant, delivered tier), tier from _tier_by_rung().
        These land nested under ``recovery[quadrant]["send"][tier]``, each
        carrying ``delivered_rung``.
      * any other kept row (payment_plan, counter_settle) -> (quadrant, kind),
        one flat cell as before -- these map 1:1 selection-to-execution.

    Returns ``{"recovery": {...}, "counts": {...}}``. ``counts`` is an exact
    partition of every action row seen (action_rows_seen == excluded_handoff +
    excluded_right_censored + excluded_null_quadrant + fitted_observations),
    plus ``send_rows_off_ladder`` -- a subset of fitted_observations, expected
    to be 0, that flags any SEND recorded at a non-buyer-facing rung.
    """
    cutoff = _run_end(days) - timedelta(days=horizon)
    tier_by_rung = _tier_by_rung()
    rung_by_tier = {name: rid for rid, name in tier_by_rung.items()}
    tier_names = set(tier_by_rung.values())

    counts = {
        "action_rows_seen": 0,
        "excluded_handoff": 0,
        "excluded_right_censored": 0,
        "excluded_null_quadrant": 0,
        "fitted_observations": 0,
        "send_rows_off_ladder": 0,
    }
    # leaf key: a SEND's delivered tier name, else the action_kind itself.
    buckets: dict[tuple[str, str], list[bool]] = defaultdict(list)

    for row in rows:
        if row["record_type"] != outcomes.ACTION_RECORD:
            continue
        counts["action_rows_seen"] += 1
        kind = row["action_kind"]
        if kind in EXCLUDED_ACTION_KINDS:
            counts["excluded_handoff"] += 1
            continue
        if date.fromisoformat(row["day"]) > cutoff:
            counts["excluded_right_censored"] += 1
            continue
        if row.get("quadrant") is None:
            counts["excluded_null_quadrant"] += 1
            continue
        counts["fitted_observations"] += 1

        if kind == SEND:
            leaf = tier_by_rung.get(row["rung"])
            if leaf is None:
                # A SEND at rung 0 or 4 -- should never happen (0 in the
                # training data). Kept in a flat "send_off_ladder" cell,
                # separate from the per-tier "send" group, never dropped.
                counts["send_rows_off_ladder"] += 1
                leaf = "send_off_ladder"
        else:
            leaf = kind
        buckets[(row["quadrant"], leaf)].append(bool(row["paid_within_horizon"]))

    nested: dict[str, dict[str, Any]] = {}
    for (quadrant, leaf), hits in buckets.items():
        successes = sum(1 for h in hits if h)
        q = nested.setdefault(quadrant, {})
        if leaf in tier_names:
            cell = _fit_cell(successes, len(hits) - successes,
                             extra={"delivered_rung": rung_by_tier[leaf]})
            q.setdefault(SEND, {})[leaf] = cell
        else:
            q[leaf] = _fit_cell(successes, len(hits) - successes)

    return {"recovery": _ordered_recovery(nested), "counts": counts}


def _ordered_recovery(nested: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deterministic key order: quadrants alphabetical; within a quadrant the
    ``send`` group first (tiers by delivered rung), then the rest alphabetical."""
    ordered: dict[str, dict[str, Any]] = {}
    for quadrant in sorted(nested):
        q = nested[quadrant]
        oq: dict[str, Any] = {}
        if SEND in q:
            oq[SEND] = {tier: q[SEND][tier]
                        for tier in sorted(q[SEND], key=lambda t: q[SEND][t]["delivered_rung"])}
        for key in sorted(k for k in q if k != SEND):
            oq[key] = q[key]
        ordered[quadrant] = oq
    return ordered


def _flatten_cells(recovery: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(quadrant, label, cell) for every fitted cell, SEND tiers as ``send/<tier>``."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for quadrant in recovery:
        for key, val in recovery[quadrant].items():
            if key == SEND:
                for tier, cell in val.items():
                    out.append((quadrant, f"send/{tier}", cell))
            else:
                out.append((quadrant, key, val))
    return out


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

_YAML_HEADER = """\
# config/learned_recovery.yaml
#
# GENERATED FILE -- produced by scripts/fit_recovery.py. Do not hand-edit;
# re-run the script instead.
#
# Recovery-probability posteriors, fitted from exploration-mode simulator runs
# on TRAINING seeds only (1000-1029). The six benchmark seeds (7, 13, 42, 99,
# 555, 2024) are held out and were NOT used.
#
# Each cell is a Beta posterior over P(payment within the attribution horizon):
#   alpha = 1 + successes,  beta = 1 + failures      (weak uniform Beta(1,1) prior)
#   mean       = alpha / (alpha + beta)              -- the posterior mean
#   ci95_width = width of the 95% central credible interval. Wide means the
#                cell is thin: the mean is not yet worth trusting.
#
# CELL LAYOUT:
#   recovery.<quadrant>.send.<tier>   -- a buyer-facing message, grouped by the
#     rung it was DELIVERED at (ladder tier: soft_nudge/firm/legal_facts), NOT
#     by the soft_nudge/firm/legal_facts label EV nominally selected. The
#     escalation walk in engine/brain.py overrides that label ~56% of the time
#     -- see docs/learning_findings.md. Each cell carries delivered_rung.
#   recovery.<quadrant>.payment_plan / .counter_settle -- one flat cell; these
#     map 1:1 from what EV selected to what was executed.
#
# READ BY engine/learning.py, behind config/rules.yaml's learning.enabled
# switch (ships OFF). recovery_probability(quadrant, "firm") resolves to
# recovery.<quadrant>.send.firm; payment_plan/counter_settle resolve directly.
#
# handoff rows are deliberately absent, not fitted as an empty cell: the
# simulator has no model of post-handoff recovery, so their outcome is
# unobservable rather than zero. See docs/learning_data.md.
#
# Full provenance -- seeds, observation counts, generation date, thin cells:
#   docs/learning_data.md
"""


def _yaml_payload(fitted: dict[str, Any], days: int, horizon: int) -> dict[str, Any]:
    counts = fitted["counts"]
    partition = {k: counts[k] for k in (
        "action_rows_seen", "excluded_handoff", "excluded_right_censored",
        "excluded_null_quadrant", "fitted_observations")}
    return {
        "version": 2,
        "meta": {
            "generated": date.today().isoformat(),
            "generator": "scripts/fit_recovery.py",
            "arm": "agent_ev_explore  (sim/run_sim.py run_agent(explore=True))",
            "training_seeds": _FlowList(TRAINING_SEEDS),
            "benchmark_seeds_held_out": _FlowList(sorted(BENCHMARK_SEEDS)),
            "simulated_days": days,
            "attribution_horizon_days": horizon,
            "prior": "Beta(1, 1) -- weak uniform",
            "excluded_action_kinds": sorted(EXCLUDED_ACTION_KINDS),
            "send_cells_grouped_by": (
                "delivered rung -> ladder tier (config/rules.yaml: 1=soft_nudge, "
                "2=firm, 3=legal_facts), NOT proposed_action_kind -- see "
                "docs/learning_findings.md"),
            "send_rows_off_ladder": counts["send_rows_off_ladder"],
            "row_partition": partition,
        },
        "recovery": fitted["recovery"],
    }


def write_yaml(fitted: dict[str, Any], days: int, horizon: int, path: Path) -> None:
    body = yaml.safe_dump(_yaml_payload(fitted, days, horizon),
                          sort_keys=False, default_flow_style=False, width=88)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_YAML_HEADER + "\n" + body, encoding="utf-8")


def _cell_table(recovery: dict[str, Any]) -> str:
    rows = []
    for quadrant, label, c in _flatten_cells(recovery):
        note = "thin" if c["observations"] < THIN_OBS else ""
        rows.append(
            f"| {quadrant} | {label} | {c['successes']} | {c['failures']} | "
            f"{c['observations']} | {c['mean']:.3f} | {c['ci95_width']:.3f} | {note} |")
    return "\n".join(rows)


def _thin_cell_lines(recovery: dict[str, Any]) -> list[str]:
    thin = [(q, label, c) for q, label, c in _flatten_cells(recovery)
            if c["observations"] < THIN_OBS]
    if not thin:
        return ["_None -- every fitted cell has at least "
                f"{THIN_OBS} observations._"]
    return [f"- `{q}` / `{label}` -- n={c['observations']} "
            f"(mean {c['mean']:.3f}, 95% CI width {c['ci95_width']:.3f})"
            for q, label, c in thin]


def write_doc(fitted: dict[str, Any], days: int, horizon: int,
              seeds: tuple[int, ...], outcomes_path: Path, path: Path) -> None:
    counts = fitted["counts"]
    run_end = _run_end(days)
    censor_cutoff = run_end - timedelta(days=horizon)
    off_ladder = counts["send_rows_off_ladder"]

    doc = f"""\
# Learning data -- provenance for `config/learned_recovery.yaml`

This file is the answer to "what did you train on?". It is written by
`scripts/fit_recovery.py` in the same run that writes the YAML, so the two
cannot drift apart. Analysis notes and known limitations are separate --
see `docs/learning_findings.md`.

- **Generated:** {date.today().isoformat()}
- **Generator:** `scripts/fit_recovery.py`
- **Arm:** `agent_ev_explore` -- `sim/run_sim.py` `run_agent(explore=True)`,
  exploration mode: the brain samples uniformly from the already-gated
  eligible-action list instead of taking the top-EV pick, so every
  (quadrant, action) cell the rules allow gets observed.
- **Reproduce:** `python scripts/fit_recovery.py` (or `--skip-run` to
  re-aggregate the existing `{_rel(outcomes_path)}` without new sim runs).

## Seeds

**Training seeds ({len(seeds)}):** {", ".join(str(s) for s in seeds)}

**Benchmark seeds, HELD OUT (never fitted on):** \
{", ".join(str(s) for s in sorted(BENCHMARK_SEEDS))}

The two sets are asserted disjoint before any run starts. Every headline
number in `report/out/results.json` is measured on the benchmark seeds;
fitting on one would let these posteriors memorise the world they are later
evaluated against.

## Parameters

| Parameter | Value |
| --- | --- |
| Simulated days per seed | {days} |
| Attribution horizon | {horizon} days |
| Run window | {SIMULATION_START.isoformat()} .. {run_end.isoformat()} |
| Right-censoring cutoff | action on or before {censor_cutoff.isoformat()} |
| Prior | Beta(1, 1), weak uniform |
| Outcomes ledger | `{_rel(outcomes_path)}` |

## Cell grouping

`payment_plan` and `counter_settle` each get one flat cell per quadrant --
they map 1:1 from what EV selected to what was executed.

A **SEND** is grouped by the rung it was **delivered** at, mapped to a tier
name through `config/rules.yaml`'s ladder (rung 1 = `soft_nudge`, 2 = `firm`,
3 = `legal_facts`), and stored nested under `recovery.<quadrant>.send.<tier>`.
It is **not** grouped by `proposed_action_kind` (the `soft_nudge`/`firm`/
`legal_facts` label EV nominally selected): the escalation walk in
`engine/brain.py` sets the delivered rung independently, and the two disagreed
on 56% of SEND rows in this training set. See `docs/learning_findings.md` for
the label/execution-gap write-up.

`send_rows_off_ladder`: **{off_ladder}** (a SEND recorded at rung 0 or 4 --
kept in a coarse `send` cell rather than dropped; expected to be 0).

## Observation counts

Exact partition of every action row produced across all training seeds:

| Bucket | Rows |
| --- | ---: |
| Action rows seen | {counts['action_rows_seen']} |
| Excluded -- handoff (unobservable outcome) | {counts['excluded_handoff']} |
| Excluded -- right-censored | {counts['excluded_right_censored']} |
| Excluded -- null quadrant | {counts['excluded_null_quadrant']} |
| **Fitted observations** | **{counts['fitted_observations']}** |

## Fitted cells

| Quadrant | Cell | Successes | Failures | Obs | Posterior mean | 95% CI width | Note |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
{_cell_table(fitted["recovery"])}

A wide CI is the honest signal that a cell is thin -- with the Beta(1,1)
prior a cell of zero observations reads as mean 0.500, CI width ~0.95.

### Thin cells (n below {THIN_OBS})

The escalation walk rarely stops at rung 1, so the `soft_nudge` (rung-1
delivery) SEND cells are thin. Their point estimates sit near the prior; the
`ci95_width` is what says so. A quadrant with **no** rung-1 sends at all has no
`soft_nudge` cell -- `engine/learning.py` falls back to the hand-typed grid
value for it (logged once).

{chr(10).join(_thin_cell_lines(fitted["recovery"]))}

## Notes

- **`config/learned_recovery.yaml` is read by `engine/learning.py`**, behind
  `config/rules.yaml`'s `learning.enabled` switch, which ships OFF. With the
  switch off nothing consults it and behaviour is byte-identical to before it
  existed.
- **handoff rows are excluded, not fitted as a zero cell.** The simulator has
  no model of what the owner does after taking a case over, so no money can
  ever land behind a handoff here -- that is an unobservable outcome, not
  evidence that handoffs fail.
- **`legal_escalation` never appears as its own action_kind.** Both it and
  `human_handoff` execute as a rung-4 `handoff` (`engine/brain.py`), so they
  fall under the handoff exclusion above.
- **Right-censored actions are dropped** rather than counted as failures:
  their horizon ran past the end of the simulated window, so a "no payment"
  verdict on them is not yet earned. With a 120-day window and a 14-day
  horizon this rarely fires -- the training worlds run out of overdue invoices
  well before the cutoff -- but the exclusion is applied regardless.
- **Side effects are restored.** A full run re-runs the simulator, which
  regenerates `data/seed/` per seed and rewrites the audit trail; the script
  snapshots both and puts them back before it exits. `--skip-run` touches
  neither.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(
        description="Fit recovery-probability posteriors from exploration-mode training "
                    "runs (SEND cells split by delivered rung; payment_plan/counter_settle "
                    "flat). Writes config/learned_recovery.yaml + docs/learning_data.md, "
                    "read by engine/learning.py behind config/rules.yaml's learning.enabled "
                    "(ships off).")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"simulated days per training seed (default: {DEFAULT_DAYS})")
    parser.add_argument("--horizon", type=int, default=None,
                        help="attribution horizon in days (default: config "
                             "learning.attribution_horizon_days)")
    parser.add_argument("--outcomes-path", type=Path, default=DEFAULT_OUTCOMES_PATH,
                        help="where the training outcome ledger is written "
                             "(default: audit/outcomes_train.jsonl -- NOT the production "
                             "audit/outcomes.jsonl)")
    parser.add_argument("--yaml-out", type=Path, default=DEFAULT_YAML_OUT,
                        help="learned posteriors output (default: config/learned_recovery.yaml)")
    parser.add_argument("--doc-out", type=Path, default=DEFAULT_DOC_OUT,
                        help="provenance record output (default: docs/learning_data.md)")
    parser.add_argument("--skip-run", action="store_true",
                        help="do not re-run the simulations; fit from the existing "
                             "--outcomes-path (for iterating on the fit itself)")
    args = parser.parse_args()

    overlap = set(TRAINING_SEEDS) & BENCHMARK_SEEDS
    if overlap:
        print(f"ABORT: training seeds overlap benchmark seeds {sorted(overlap)} -- "
              f"benchmark seeds must never be fitted on")
        return 1

    horizon = args.horizon if args.horizon is not None else outcomes.horizon_days()

    print(f"fit_recovery: {len(TRAINING_SEEDS)} training seeds {TRAINING_SEEDS[0]}-"
          f"{TRAINING_SEEDS[-1]}, {args.days} days each, horizon {horizon}d")
    print(f"  benchmark seeds held out: {sorted(BENCHMARK_SEEDS)}")

    if args.skip_run:
        print(f"  --skip-run: fitting from existing {args.outcomes_path}")
    else:
        print(f"  running exploration mode -> {args.outcomes_path}")
        _run_training(TRAINING_SEEDS, args.days, args.outcomes_path)

    rows = outcomes.records(args.outcomes_path)
    if not rows:
        print(f"no rows in {args.outcomes_path} -- nothing to fit")
        return 1

    fitted = fit(rows, args.days, horizon)
    counts = fitted["counts"]
    print(f"  action rows: {counts['action_rows_seen']} seen, "
          f"{counts['fitted_observations']} fitted "
          f"({counts['excluded_handoff']} handoff, "
          f"{counts['excluded_right_censored']} censored, "
          f"{counts['excluded_null_quadrant']} null-quadrant excluded)")
    if counts["send_rows_off_ladder"]:
        print(f"  !! {counts['send_rows_off_ladder']} SEND row(s) at a non-buyer-facing "
              f"rung -- kept in a coarse 'send' cell")

    write_yaml(fitted, args.days, horizon, args.yaml_out)
    write_doc(fitted, args.days, horizon, TRAINING_SEEDS, args.outcomes_path, args.doc_out)
    print(f"  wrote {_rel(args.yaml_out)}")
    print(f"  wrote {_rel(args.doc_out)}")

    print()
    for quadrant, label, c in _flatten_cells(fitted["recovery"]):
        thin = "  THIN" if c["observations"] < THIN_OBS else ""
        print(f"  {quadrant:<18} {label:<18} "
              f"mean {c['mean']:.3f}  ci95 {c['ci95_width']:.3f}  "
              f"(n={c['observations']}: {c['successes']}/{c['failures']}){thin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
