"""Fake data factory -- builds the test world: ~20 buyers, ~100 invoices.

Synthetic on purpose: the track allows it, and it means no API access and no
privacy concerns can block the build.

Messy on purpose too: invoices with no written agreement (the 15-day rule
applies), partial payments, at least one dispute, buyers with only one or two
invoices of history (low confidence), amounts from Rs 8,000 to Rs 12,00,000.

Money is stored in integer paise. Output lands in data/seed/.

    python data/generate.py --seed 42

Day 2.
"""

from __future__ import annotations

import argparse
from typing import Any

DEFAULT_SEED = 42
DEFAULT_BUYERS = 20
DEFAULT_INVOICES = 100


def generate(seed: int, n_buyers: int = DEFAULT_BUYERS, n_invoices: int = DEFAULT_INVOICES) -> dict[str, Any]:
    """Build the whole test world reproducibly from one seed."""
    raise NotImplementedError("data factory")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic buyers and invoices.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: 42)")
    parser.add_argument("--buyers", type=int, default=DEFAULT_BUYERS)
    parser.add_argument("--invoices", type=int, default=DEFAULT_INVOICES)
    args = parser.parse_args()
    print(f"data factory: not implemented (seed={args.seed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
