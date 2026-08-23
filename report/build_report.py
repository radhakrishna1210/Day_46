"""Scoreboard -- turns simulation results into the HTML report (Jinja2).

The star slide: baseline vs agent on the same seeded invoices.

    rupees recovered, average days to pay, messages sent,
    correctly escalated to a human, and not recovered

Plus the exceptions list -- every invoice we failed to recover and why. Honesty
about failures is in the judging bar, so it is a first-class output, not a
footnote.

    python report/build_report.py

Day 10.
"""

from __future__ import annotations

import argparse
from typing import Any


def build(results: dict[str, Any], out_path: str) -> str:
    """Render the comparison report to HTML and return the path written."""
    raise NotImplementedError("step 9: report")


def exceptions_list(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Every invoice not recovered, with the reason and the buyer persona."""
    raise NotImplementedError("step 9: report")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the baseline-vs-agent HTML report.")
    parser.add_argument("--out", default="report/out/report.html", help="where to write the report")
    args = parser.parse_args()
    print(f"report builder: not implemented (out={args.out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
