"""Simulator -- runs the agent (and the baseline) day by day over the test world.

The baseline is a dumb reminder bot: three fixed reminders, same message for
everyone, roughly what payment-link reminders do today. Both run on the SAME
invoices with the SAME seed, so the comparison is honest.

    python sim/run_sim.py --compare --seed 42

Day 8-9.
"""

from __future__ import annotations

import argparse
from typing import Any

DEFAULT_SEED = 42
DEFAULT_DAYS = 90


def run_agent(seed: int, days: int) -> dict[str, Any]:
    """Run the full agent over the simulated window."""
    raise NotImplementedError("step 8: simulator")


def run_baseline(seed: int, days: int) -> dict[str, Any]:
    """Run the dumb three-fixed-reminders baseline over the same window."""
    raise NotImplementedError("step 8: simulator")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the recovery simulation.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: 42)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="simulated days to run")
    parser.add_argument("--compare", action="store_true", help="run baseline and agent side by side")
    args = parser.parse_args()
    mode = "baseline vs agent" if args.compare else "agent only"
    print(f"simulator: not implemented (seed={args.seed}, days={args.days}, mode={mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
