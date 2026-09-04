"""Regenerate every committed artifact from seed 7, in the one correct order.

Five files are committed on purpose so a reviewer sees the measured result
without running the ~3.5-minute simulation:

    report/out/results.json     the four-arm benchmark + multi-seed table
    report/out/report.html      the rendered scoreboard
    report/out/dashboard.html   the Receivables Command Center
    report/out/dashboard.json   its embedded payload, extracted
    docs/index.html             the GitHub Pages copy of dashboard.html

All five are downstream of ONE 120-day, six-seed simulation and must be
rebuilt together, in this order:

    1. sim/run_sim.py --compare  -> results.json  + rewrites audit/audit_log.jsonl
    2. report/build_report.py    -> report.html   (reads results.json AND the trail)
    3. scripts/build_dashboard.py -> dashboard.{html,json} + docs/index.html

Steps 2 and 3 both read audit/audit_log.jsonl, and that file is NOT
hermetic: whatever process wrote it last wins. Running --compare first
leaves the trail in the exact state the report and dashboard builders
expect (seed 7, the plain-agent arm, 120 simulated days). Run anything
else against the repo in between -- pytest, `python main.py`, a second
simulation -- and the report is silently built against the wrong trail.
That non-hermeticity is the whole reason this script exists: it is the
only safe way to rebuild the committed set.

Usage:
    python scripts/regen.py

The script stops at the first failing step (loudly), and at the end prints
which of the five artifacts changed and still need committing. It never
runs `git add`, `git commit`, or `git push` -- committing is a separate,
deliberate step you take after reading the diff.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The seed the committed artifacts are built on. Changing this here is not
#: enough to move the demo to another seed -- the four "numbers to say out
#: loud" in docs/demo_runbook.md and every hardcoded figure in the tracked
#: .md files are seed 7 too.
SEED = 7

#: Kept identical to docs/demo_runbook.md's locked Segment 2 command and to
#: .gitignore's regenerate note. Seed 7 is primary (its narrative lands in
#: results.json); 42 is demoted to an extra so the six-seed aggregate is
#: unchanged. sim/run_sim.py's own default is still --seed 42.
EXTRA_SEEDS = "42,13,99,2024,555"
DAYS = "120"

#: The exact five files this script is responsible for. Anything else that
#: shows up dirty afterwards was not written by this run.
ARTIFACTS = (
    "report/out/results.json",
    "report/out/report.html",
    "report/out/dashboard.html",
    "report/out/dashboard.json",
    "docs/index.html",
)

STEPS: tuple[tuple[str, list[str]], ...] = (
    (
        "simulation (six seeds x four arms x 120 days)",
        ["sim/run_sim.py", "--compare", "--seed", str(SEED),
         "--extra-seeds", EXTRA_SEEDS, "--days", DAYS],
    ),
    (
        "scoreboard report",
        ["report/build_report.py"],
    ),
    (
        "dashboard (writes report/out/dashboard.* AND docs/index.html)",
        ["scripts/build_dashboard.py", "--seed", str(SEED)],
    ),
)


def _run_step(number: int, label: str, argv: list[str]) -> None:
    """Run one pipeline step, streaming its output. Abort the whole script
    on a non-zero exit -- a half-regenerated artifact set is worse than none."""
    printed = " ".join([Path(sys.executable).name, *argv])
    print(f"\n=== step {number}/{len(STEPS)}: {label} ===")
    print(f"    {printed}", flush=True)
    started = time.monotonic()
    result = subprocess.run([sys.executable, *argv], cwd=ROOT)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        print(
            f"\nFAILED: step {number} ({label}) exited {result.returncode} "
            f"after {elapsed:.0f}s.\n"
            f"Nothing further was run. The artifacts are now half-regenerated "
            f"and inconsistent -- fix the error above and re-run this script "
            f"from the start; do not commit them as they are.",
            file=sys.stderr,
        )
        sys.exit(result.returncode)
    print(f"    step {number} done in {elapsed:.0f}s", flush=True)


def _changed_artifacts() -> list[str]:
    """The subset of ARTIFACTS that git sees as modified (or untracked)."""
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", *ARTIFACTS],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    # porcelain line: "XY <path>" ; path starts at column 3
    return sorted(line[3:].strip() for line in out.splitlines() if line.strip())


def main() -> int:
    if len(sys.argv) > 1:
        print(__doc__)
        if sys.argv[1] in ("-h", "--help"):
            return 0
        print(f"error: regen.py takes no arguments (got {sys.argv[1:]})", file=sys.stderr)
        return 2

    overall_start = time.monotonic()
    print(f"regenerating the committed artifacts from seed {SEED}")
    print(f"repo: {ROOT}")

    for number, (label, argv) in enumerate(STEPS, start=1):
        _run_step(number, label, argv)

    total = time.monotonic() - overall_start
    changed = _changed_artifacts()

    print(f"\n=== done in {total:.0f}s ===")
    if not changed:
        print(
            "no artifact changed against the committed copies -- unusual but "
            "not impossible if the run landed in the same wall-clock minute as "
            "the last one (the embedded build timestamps are minute-resolution)."
        )
        return 0

    print("changed, and NOT yet committed:")
    for path in changed:
        print(f"  {path}")
    print(
        "\nExpected: every run rewrites an embedded `generated` timestamp, so a "
        "diff here is normal. Check the diff is timestamp-only (or an intended "
        "content change) before committing:"
    )
    print(f"  git diff -- {' '.join(changed)}")
    print(f"  git add {' '.join(changed)} && git commit")
    print("\nthis script did not stage, commit, or push anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
