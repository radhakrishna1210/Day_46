"""Revenue Recovery Agent -- one command runs the whole thing.

    python main.py --seed 42

Day 1: the pipeline skeleton. Every stage is wired in and announces itself;
none of them do any work yet. Each stage is filled in on its planned day, and
this file is the map of the system a judge reads first.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from data import generate
from engine import audit, brain, channels, law, llm, promises, score, watchdog, writer
from report import build_report
from sim import personas, run_sim

DEFAULT_SEED = 42


@dataclass(frozen=True)
class Stage:
    """One stage of the pipeline: what it is, and which module owns it."""

    name: str
    what: str
    module: object


PIPELINE: tuple[Stage, ...] = (
    Stage("data factory", "generate the synthetic buyers and invoices", generate),
    Stage("watchdog", "find the invoices that are overdue today", watchdog),
    Stage("score engine", "score each buyer from their payment history", score),
    Stage("law engine", "compute statutory due date, interest, tax exposure", law),
    Stage("brain", "pick one escalation rung, or stop", brain),
    Stage("message writer", "draft the message for the chosen rung", writer),
    Stage("promise tracker", "read replies, remember and check promises", promises),
    Stage("post office", "send the email, log the stubbed channels", channels),
    Stage("simulator", "run baseline and agent over the same seeded world", run_sim),
    Stage("scoreboard", "build the comparison report and exceptions list", build_report),
)


def run(seed: int) -> int:
    """Walk the pipeline. Returns a process exit code."""
    print(f"revenue recovery agent: starting (seed={seed}, llm_mode={llm.get_mode()})")
    for number, stage in enumerate(PIPELINE, start=1):
        print(f"step {number}: {stage.name} -- {stage.what} -- not implemented")
    print(f"audit trail ({audit.__name__}): {len(PIPELINE)} stages announced, 0 actions taken")
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
