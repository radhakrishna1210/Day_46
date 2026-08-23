"""Revenue Recovery Agent -- one command runs the whole thing.

    python main.py --seed 42

Each stage is filled in on its planned day. Stages that are built do their work
and report it; the rest announce themselves and say so plainly, because a
pipeline that pretends to have run is worse than one that admits it has not.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from data import generate, store
from data.generate import format_inr
from engine import audit, brain, channels, law, llm, promises, score, watchdog, writer
from report import build_report
from sim import personas, run_sim

DEFAULT_SEED = 42


@dataclass
class Context:
    """What the stages hand each other as the run proceeds."""

    seed: int
    today: date
    buyers: list[dict[str, Any]]
    invoices: list[dict[str, Any]]
    queue: list[dict[str, Any]]
    scores: list[dict[str, Any]]


def _ensure_dataset(seed: int) -> None:
    """Generate the world if it is not on disk yet.

    The dataset is gitignored, so a fresh clone has none. Building it here means
    `python main.py --seed 42` works on its own rather than failing on a missing
    file; running data/generate.py yourself does exactly the same thing.
    """
    if store.dataset_exists():
        return
    print(f"  dataset not found, generating it now (seed {seed})")
    world = generate.generate(seed)
    generate._write_json(store.BUYERS_PATH, world["buyers"])
    generate._write_json(store.INVOICES_PATH, world["invoices"])
    generate._write_json(generate.DEFAULT_PERSONA_PATH, world["personas"])


# --- the stages -----------------------------------------------------------

def stage_data(context: Context) -> str:
    _ensure_dataset(context.seed)
    context.buyers = store.load_buyers()
    context.invoices = store.load_invoices()
    meta = store.load_meta()
    context.today = date.fromisoformat(meta["simulation_start"])
    current = sum(1 for inv in context.invoices if inv["cohort"] == "current")
    return f"{len(context.buyers)} buyers, {len(context.invoices)} invoices ({current} current)"


def stage_watchdog(context: Context) -> str:
    context.queue = watchdog.overdue_invoices(context.invoices, context.today)
    unsettled = [inv for inv in context.invoices if watchdog.is_unsettled(inv)]
    at_risk = sum(watchdog.outstanding_paise(inv) for inv in context.queue)
    return (
        f"{len(context.queue)} overdue of {len(unsettled)} unsettled, "
        f"{format_inr(at_risk, 'Rs ')} at risk, as of {context.today.isoformat()}"
    )


def stage_score(context: Context) -> str:
    grouped = store.invoices_by_buyer(context.invoices)
    context.scores = score.score_all(context.buyers, grouped, context.today)
    low = sum(1 for s in context.scores if s["confidence"] == "low")
    worst = context.scores[0]
    return (
        f"{len(context.scores)} buyers scored, worst {worst['buyer_id']} at "
        f"{worst['score']}/100, {low} on low confidence"
    )


def stage_law(context: Context) -> str:
    void = [inv for inv in context.queue if law.agreed_term_is_void(inv)]
    gained = sum(law.days_gained_by_law(inv) for inv in void)
    return (
        f"{len(void)} overdue invoices had void terms, worth {gained} days of "
        f"leverage; interest and 43B(h) math land on Day 4"
    )


def _pending(day: str) -> Callable[[Context], str]:
    """A stage that has not been built yet, and says so."""
    def run(_context: Context) -> str:
        return f"not implemented ({day})"
    run.pending = True                                      # type: ignore[attr-defined]
    return run


@dataclass(frozen=True)
class Stage:
    """One stage of the pipeline: what it is, and what it does."""

    name: str
    what: str
    run: Callable[[Context], str]


PIPELINE: tuple[Stage, ...] = (
    Stage("data factory", "load or build the synthetic world", stage_data),
    Stage("watchdog", "find the invoices that are overdue today", stage_watchdog),
    Stage("score engine", "score each buyer from their payment history", stage_score),
    Stage("law engine", "statutory due dates, interest, tax exposure", stage_law),
    Stage("brain", "pick one escalation rung, or stop", _pending("Day 6")),
    Stage("message writer", "draft the message for the chosen rung", _pending("Day 6")),
    Stage("promise tracker", "read replies, remember and check promises", _pending("Day 7")),
    Stage("post office", "send the email, log the stubbed channels", _pending("Day 7")),
    Stage("simulator", "run baseline and agent over the same seeded world", _pending("Day 8")),
    Stage("scoreboard", "build the comparison report and exceptions list", _pending("Day 10")),
)


def run(seed: int) -> int:
    """Walk the pipeline. Returns a process exit code."""
    print(f"revenue recovery agent: starting (seed={seed}, llm_mode={llm.get_mode()})")
    context = Context(seed=seed, today=date.today(), buyers=[], invoices=[], queue=[], scores=[])

    for number, stage in enumerate(PIPELINE, start=1):
        print(f"step {number}: {stage.name} -- {stage.what}")
        print(f"  {stage.run(context)}")

    built = sum(1 for stage in PIPELINE if not getattr(stage.run, "pending", False))
    print(f"audit trail ({audit.__name__}): {built} of {len(PIPELINE)} stages doing real work")
    print("revenue recovery agent: done")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the full revenue recovery simulation end to end.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"random seed, for reproducible runs (default: {DEFAULT_SEED})",
    )
    args = parser.parse_args()
    return run(args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
