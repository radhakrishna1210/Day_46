"""Buyer score -- payment history compressed into one explainable number.

Rules, never AI. A score that decides how hard to chase someone for money has
to be defensible line by line, so every score carries the arithmetic that
produced it and every weight comes from config/rules.yaml.

    score = base
            - (average_delay_days * avg_delay_penalty)
            - (broken_promises    * broken_promise_penalty)
            - (disputes_raised    * dispute_penalty)
            + (on_time_streak     * on_time_bonus)
    clamped to [min, max]

Delay is measured against the STATUTORY due date, not the date the contract
claimed -- a buyer who took the 90 days their contract promised them is still
45 days late in the eyes of the Act, and the score says so.

Confidence matters as much as the score. Two invoices of history is not
evidence, and the brain is expected to tread carefully when it says "low".

    python engine/score.py
    python engine/score.py --explain BUY-07
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it, by
# putting the repo root on the path when there is no enclosing package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.config import rules
from engine.law import _as_date, statutory_due_date


def payment_delay_days(invoice: dict[str, Any]) -> int:
    """Days between the statutory due date and the day the money actually landed.

    Negative means they paid early. Signed on purpose: a buyer who consistently
    pays a week early has earned the credit that gives them.
    """
    return (_as_date(invoice["paid_date"]) - statutory_due_date(invoice)).days


def settled_history(invoices: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Paid invoices, newest first. Optionally only those settled before a date."""
    history = [
        inv for inv in invoices
        if inv.get("status") == "paid" and inv.get("paid_date")
    ]
    if today is not None:
        history = [inv for inv in history if _as_date(inv["paid_date"]) < today]
    history.sort(key=lambda inv: _as_date(inv["paid_date"]), reverse=True)
    return history


def confidence(paid_invoice_count: int) -> str:
    """Map how much history we have onto low / medium / high."""
    thresholds = rules()["score"]["confidence"]
    if paid_invoice_count < int(thresholds["low_below_invoices"]):
        return "low"
    if paid_invoice_count >= int(thresholds["high_from_invoices"]):
        return "high"
    return "medium"


def on_time_streak(history: list[dict[str, Any]]) -> int:
    """How many of the most recent invoices in a row were paid on or before time."""
    streak = 0
    for invoice in history:                     # already newest first
        if payment_delay_days(invoice) > 0:
            break
        streak += 1
    return streak


def signals(history: list[dict[str, Any]]) -> dict[str, Any]:
    """The four raw facts the score is built from."""
    if not history:
        return {
            "average_delay_days": 0.0,
            "worst_delay_days": 0,
            "broken_promises": 0,
            "disputes_raised": 0,
            "on_time_streak": 0,
        }
    delays = [payment_delay_days(inv) for inv in history]
    return {
        "average_delay_days": round(sum(delays) / len(delays), 1),
        "worst_delay_days": max(delays),
        "broken_promises": sum(1 for inv in history if inv.get("promise_broken")),
        "disputes_raised": sum(1 for inv in history if inv.get("disputed")),
        "on_time_streak": on_time_streak(history),
    }


def _score_from_history(history: list[dict[str, Any]]) -> tuple[int, dict[str, Any], list[dict[str, Any]]]:
    """Run the formula. Returns the score, the raw signals, and the arithmetic."""
    config = rules()["score"]
    weights = config["weights"]
    base = float(config["base"])
    low, high = float(config["min"]), float(config["max"])

    raw = signals(history)
    terms = [
        ("average delay", f"{raw['average_delay_days']} days late on average",
         -raw["average_delay_days"] * float(weights["avg_delay_penalty"])),
        ("broken promises", f"{raw['broken_promises']} promise(s) not kept",
         -raw["broken_promises"] * float(weights["broken_promise_penalty"])),
        ("disputes raised", f"{raw['disputes_raised']} dispute(s) on past invoices",
         -raw["disputes_raised"] * float(weights["dispute_penalty"])),
        ("on-time streak", f"{raw['on_time_streak']} recent invoice(s) paid on time",
         raw["on_time_streak"] * float(weights["on_time_bonus"])),
    ]

    breakdown = [{"factor": "starting score", "detail": "every buyer starts here", "points": base}]
    breakdown += [
        {"factor": factor, "detail": detail, "points": round(points, 1)}
        for factor, detail, points in terms
    ]

    unclamped = base + sum(points for _factor, _detail, points in terms)
    score = int(round(min(high, max(low, unclamped))))
    if unclamped < low or unclamped > high:
        breakdown.append({
            "factor": "clamped",
            "detail": f"raw score {round(unclamped, 1)} pulled inside {int(low)}-{int(high)}",
            "points": round(score - unclamped, 1),
        })
    return score, raw, breakdown


def _trend(invoices: list[dict[str, Any]], today: date, current: int) -> dict[str, Any]:
    """Compare today's score with the score six months ago.

    Unknown rather than steady when there is not enough old history to compare
    -- claiming a flat trend from one data point would be a lie.
    """
    config = rules()["score"]["trend"]
    window_days = int(config["window_days"])
    noise_floor = float(config["noise_floor"])

    cutoff = today - timedelta(days=window_days)
    earlier_history = settled_history(invoices, today=cutoff)
    if len(earlier_history) < 2:
        return {
            "direction": "unknown",
            "earlier_score": None,
            "delta": None,
            "window_days": window_days,
            "detail": "not enough history before the comparison window",
        }

    earlier, _raw, _breakdown = _score_from_history(earlier_history)
    delta = current - earlier
    if delta > noise_floor:
        direction = "improving"
    elif delta < -noise_floor:
        direction = "worsening"
    else:
        direction = "steady"
    return {
        "direction": direction,
        "earlier_score": earlier,
        "delta": delta,
        "window_days": window_days,
        "detail": f"{earlier} six months ago, {current} now",
    }


def score_buyer(
    buyer: dict[str, Any],
    invoices: list[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    """Score one buyer 0-100 from their payment history.

    Args:
        buyer: the buyer record.
        invoices: that buyer's invoices. Unpaid ones are ignored -- an invoice
            still running is not yet evidence of anything.
        today: the simulation clock.

    Returns:
        The score, a confidence level, the raw signals, the arithmetic that
        produced the number, and a six-month trend. Everything a human needs to
        argue with the result.
    """
    history = settled_history(invoices, today=today)
    score, raw, breakdown = _score_from_history(history)
    level = confidence(len(history))

    if not history:
        breakdown.append({
            "factor": "no history",
            "detail": "no settled invoices yet; this is the neutral default, not a good record",
            "points": 0.0,
        })

    return {
        "buyer_id": buyer["buyer_id"],
        "name": buyer.get("name"),
        "score": score,
        "confidence": level,
        "history_count": len(history),
        "signals": raw,
        "breakdown": breakdown,
        "trend": _trend(invoices, today, score),
        "as_of": today.isoformat(),
    }


def score_all(
    buyers: list[dict[str, Any]],
    invoices_by_buyer: dict[str, list[dict[str, Any]]],
    today: date,
) -> list[dict[str, Any]]:
    """Score every buyer, worst first -- the ones that need attention lead."""
    scored = [
        score_buyer(buyer, invoices_by_buyer.get(buyer["buyer_id"], []), today)
        for buyer in buyers
    ]
    scored.sort(key=lambda s: (s["score"], s["buyer_id"]))
    return scored


def explain(scored: dict[str, Any]) -> str:
    """The score as a human-readable paragraph, for logs and the audit trail."""
    lines = [
        f"{scored['buyer_id']} {scored['name']}",
        f"  score {scored['score']}/100  confidence {scored['confidence']} "
        f"({scored['history_count']} settled invoices)",
        f"  trend {scored['trend']['direction']}: {scored['trend']['detail']}",
        "  how it was calculated:",
    ]
    for item in scored["breakdown"]:
        points = item["points"]
        sign = "+" if points > 0 else ""
        lines.append(f"    {sign}{points:>7}  {item['factor']:<18} {item['detail']}")
    return "\n".join(lines)


def main() -> int:
    from data import store

    from engine.money import enable_unicode_output

    enable_unicode_output()
    parser = argparse.ArgumentParser(description="Score every buyer from payment history.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="simulation date (default: the dataset's simulation_start)")
    parser.add_argument("--explain", metavar="BUYER_ID", default=None,
                        help="print the full arithmetic for one buyer")
    args = parser.parse_args()

    if not store.dataset_exists():
        print(f"no dataset found -- {store.REGENERATE_HINT}")
        return 1

    buyers = store.load_buyers()
    invoices = store.load_invoices()
    grouped = store.invoices_by_buyer(invoices)
    today = args.as_of or _as_date(store.load_meta()["simulation_start"])
    scored = score_all(buyers, grouped, today)

    if args.explain:
        match = next((s for s in scored if s["buyer_id"] == args.explain), None)
        if match is None:
            print(f"no such buyer: {args.explain}")
            return 1
        print(explain(match))
        return 0

    print(f"buyer scores as of {today.isoformat()}")
    print(f"  {'buyer':<9}{'score':>6}{'conf':>8}{'seen':>6}{'avg delay':>11}{'broken':>8}{'trend':>11}  name")
    for item in scored:
        signal = item["signals"]
        print(
            f"  {item['buyer_id']:<9}{item['score']:>6}{item['confidence']:>8}"
            f"{item['history_count']:>6}{signal['average_delay_days']:>10}d"
            f"{signal['broken_promises']:>8}{item['trend']['direction']:>11}  {item['name']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
