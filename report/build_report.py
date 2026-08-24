"""Scoreboard -- turns simulation results into the HTML report (Jinja2).

The star slide: baseline vs agent on the same seeded invoices.

    rupees recovered, average days to pay, messages sent,
    correctly escalated to a human, and not recovered

Plus the exceptions list -- every invoice we failed to recover and why. Honesty
about failures is in the judging bar, so it is a first-class output, not a
footnote.

Reads the results.json that `sim/run_sim.py --compare` writes, so nothing here
re-runs the simulation or touches any random number -- this module only
formats what already happened.

    python report/build_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine import audit
from engine.money import format_inr

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "report" / "templates"
DEFAULT_RESULTS_PATH = ROOT / "report" / "out" / "results.json"
DEFAULT_OUT_PATH = ROOT / "report" / "out" / "report.html"

#: How many of the most recent audit-trail lines the report shows. The full
#: trail (potentially thousands of lines over a 120-day agent run) lives at
#: audit/audit_log.jsonl for anyone who wants to check further.
AUDIT_EXCERPT_LINES = 20


class ResultsMissing(FileNotFoundError):
    """Raised when results.json has not been produced yet."""


def _money(paise: int) -> str:
    return format_inr(int(paise))


def _days(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "n/a"


def exceptions_list(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Every invoice the AGENT failed to recover, with the reason and persona.

    This is our own result's honesty section -- ARCHITECTURE.md's Scoreboard
    block is explicit that the exceptions list is about what WE could not
    recover, not a scorecard of the baseline's failures too.
    """
    return results["agent"]["exceptions"]


def _headline_rows(results: dict[str, Any]) -> list[dict[str, str]]:
    baseline, agent = results["baseline"], results["agent"]
    matched = results.get("matched_avg_days_to_pay") or {"n": 0, "baseline": None, "agent": None}
    return [
        {"label": "₹ recovered",
         "baseline": _money(baseline["final"]["recovered_paise"]),
         "agent": _money(agent["final"]["recovered_paise"])},
        {"label": "Avg days to pay (each run's own recovered invoices)",
         "baseline": _days(baseline["avg_days_to_pay"]),
         "agent": _days(agent["avg_days_to_pay"])},
        {"label": f"Avg days to pay ({matched['n']} invoices BOTH recovered -- the fair comparison)",
         "baseline": _days(matched["baseline"]), "agent": _days(matched["agent"])},
        {"label": "Messages sent",
         "baseline": str(baseline["messages_sent"]), "agent": str(agent["messages_sent"])},
        {"label": "Escalated to a human",
         "baseline": str(baseline["handoffs"]), "agent": str(agent["handoffs"])},
        {"label": "Not recovered",
         "baseline": str(len(baseline["exceptions"])), "agent": str(len(agent["exceptions"]))},
    ]


def _per_rung_rows(results: dict[str, Any]) -> list[dict[str, Any]]:
    per_rung = results["agent"]["per_rung"]
    names = {"1": "1 -- soft nudge", "2": "2 -- firm", "3": "3 -- legal facts"}
    rows = []
    for rung_id in sorted(per_rung, key=int):
        row = per_rung[rung_id]
        rows.append({
            "rung": names.get(str(rung_id), str(rung_id)),
            "invoices_contacted": row["invoices_contacted"],
            "recovered_here": row["recovered_here"],
            "effectiveness_pct": row["effectiveness_pct"],
        })
    return rows


def _per_attempt_rows(results: dict[str, Any]) -> list[dict[str, Any]]:
    per_attempt = results["baseline"]["per_attempt"]
    rows = []
    for n in sorted(per_attempt, key=int):
        row = per_attempt[n]
        rows.append({
            "attempt": f"reminder {n}",
            "invoices_contacted": row["invoices_contacted"],
            "recovered_here": row["recovered_here"],
            "effectiveness_pct": row["effectiveness_pct"],
        })
    return rows


def _exception_rows(results: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in exceptions_list(results):
        rows.append({**item, "outstanding": _money(item["outstanding_paise"])})
    return rows


def _audit_excerpt() -> list[dict[str, Any]]:
    entries = audit.entries()
    return entries[-AUDIT_EXCERPT_LINES:]


def _view(results: dict[str, Any]) -> dict[str, Any]:
    handoff_reasons = results["agent"].get("handoff_reasons") or {}
    stop_reasons = results["agent"].get("stop_reasons") or {}
    return {
        "seed": results["seed"],
        "days": results["days"],
        "generated": results.get("generated"),
        "headline": _headline_rows(results),
        "per_rung": _per_rung_rows(results),
        "per_attempt": _per_attempt_rows(results),
        "exceptions": _exception_rows(results),
        "handoff_reasons": sorted(handoff_reasons.items()),
        "stop_reasons": sorted(stop_reasons.items()),
        "audit_excerpt": _audit_excerpt(),
        "gain_paise": (results["agent"]["final"]["recovered_paise"]
                      - results["baseline"]["final"]["recovered_paise"]),
        "gain": _money(results["agent"]["final"]["recovered_paise"]
                      - results["baseline"]["final"]["recovered_paise"]),
        "message_delta": (results["agent"]["messages_sent"] - results["baseline"]["messages_sent"]),
    }


def build(results: dict[str, Any], out_path: str) -> str:
    """Render the comparison report to HTML and return the path written.

    Args:
        results: the payload sim/run_sim.py --compare writes to results.json.
        out_path: where to write the rendered HTML.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report.html.j2")
    html = template.render(**_view(results))

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return str(path)


def load_results(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ResultsMissing(
            f"{path} not found -- run: python sim/run_sim.py --compare --seed 42 --days 120"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the baseline-vs-agent HTML report.")
    parser.add_argument("--in", dest="results_in", default=str(DEFAULT_RESULTS_PATH),
                        help="results.json to read (default: report/out/results.json)")
    parser.add_argument("--out", default=str(DEFAULT_OUT_PATH),
                        help="where to write the report (default: report/out/report.html)")
    args = parser.parse_args()

    try:
        results = load_results(Path(args.results_in))
    except ResultsMissing as exc:
        print(str(exc))
        return 1

    path = build(results, args.out)
    print(f"report written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
