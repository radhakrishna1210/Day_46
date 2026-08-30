"""Two-axis buyer score -- CAN they pay, and WILL they?

Rules, never AI, exactly like engine/score.py: every number here carries the
arithmetic that produced it and every weight comes from config/rules.yaml.

The legacy score in engine/score.py answers one blurred question -- "how much
trouble is this buyer?" -- and it cannot tell apart the two situations that
deserve opposite treatment:

    a buyer who CANNOT pay        -> a payment plan is the honest move
    a buyer who WILL not pay      -> firmer, faster escalation is

Both look identical in payment history alone: both pay late, both break
promises. Telling them apart needs evidence about the buyer's money coming
IN, which is what data/generate.py's monthly_inflow_paise and
failed_payment_count exist to provide.

    WILLINGNESS  a relabel of the legacy formula -- delay, broken promises,
                 disputes, on-time streak. Same signals, same weights (its
                 own copy of them in config, so Phase 2 can tune the two
                 apart without moving the legacy number every other module
                 already reads).

    ABILITY      the genuinely new axis -- inflow trend, inflow volatility,
                 failed payments, and (when a specific invoice is being
                 judged) that invoice's size against a typical recent month.

Together they place the buyer in one of four quadrants:

                        WILLINGNESS
                    low             high
                +---------------+---------------+
        high    | can_pay_but   | good_customer |
                | _wont         |               |
    ABILITY     +---------------+---------------+
        low     | high_risk     | cash_flow     |
                |               | _problem      |
                +---------------+---------------+

PHASE 1 SCOPE, stated plainly: this module is computed and explained, and
nothing acts on it. engine/brain.py does not import it, no message changes,
no escalation changes. Wiring the quadrant into decisions is Phase 2. The
point of shipping it inert first is that the numbers can be inspected and
argued with before they are allowed to move money.

Nothing here ever sees a persona tag. The generator correlates the inflow
signals with the hidden persona, and only the NUMBERS land on the buyer
record -- the same one-way street payment delays already travel down
(tests/test_sim_isolation.py enforces it for every module in engine/).

    python engine/ability_willingness.py
    python engine/ability_willingness.py --explain BUY-07
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it, by
# putting the repo root on the path when there is no enclosing package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import score as score_engine
from engine.config import rules
from engine.law import _as_date

#: The four quadrants, so callers can name them without spelling strings.
GOOD_CUSTOMER = "good_customer"
CASH_FLOW_PROBLEM = "cash_flow_problem"
CAN_PAY_BUT_WONT = "can_pay_but_wont"
HIGH_RISK = "high_risk"

QUADRANTS: tuple[str, ...] = (GOOD_CUSTOMER, CASH_FLOW_PROBLEM, CAN_PAY_BUT_WONT, HIGH_RISK)

#: Plain-English gloss for each quadrant, for logs and the audit trail.
QUADRANT_MEANING: dict[str, str] = {
    GOOD_CUSTOMER: "can pay and does pay",
    CASH_FLOW_PROBLEM: "wants to pay but the money is not there",
    CAN_PAY_BUT_WONT: "has the money and is choosing not to pay",
    HIGH_RISK: "neither the means nor the intent",
}


def _clamp(value: float, low: float, high: float) -> int:
    return int(round(min(high, max(low, value))))


def inflow_series(buyer: dict[str, Any]) -> list[int]:
    """The buyer's monthly money-in, oldest first, defensively cleaned.

    A buyer record from before this field existed (schema_version 1), or one
    that is malformed, yields an empty list rather than an exception -- the
    ability score degrades to its neutral base and says so, the same way the
    legacy score reports "no history" instead of inventing one.
    """
    raw = buyer.get("monthly_inflow_paise")
    if not isinstance(raw, (list, tuple)):
        return []
    return [int(v) for v in raw if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0]


def failed_payment_count(buyer: dict[str, Any]) -> int:
    """Bounced/failed payment attempts on record, 0 when unknown or malformed."""
    raw = buyer.get("failed_payment_count")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def inflow_trend_pct(series: list[int]) -> float | None:
    """Percent change from the first half of the series to the second half.

    None when there is not enough of a series to compare -- two months is not
    a trend, and saying "flat" from two points would be the same lie
    engine/score.py's trend refuses to tell.
    """
    minimum = int(rules()["score"]["ability"]["min_months_for_trend"])
    if len(series) < minimum:
        return None
    half = len(series) // 2
    older, newer = series[:half], series[half:]
    older_mean = statistics.fmean(older)
    if older_mean <= 0:
        return None
    return round((statistics.fmean(newer) - older_mean) / older_mean * 100, 1)


def inflow_volatility_pct(series: list[int]) -> float | None:
    """Standard deviation as a percent of the mean. None below two months."""
    if len(series) < 2:
        return None
    mean = statistics.fmean(series)
    if mean <= 0:
        return None
    return round(statistics.stdev(series) / mean * 100, 1)


def typical_monthly_inflow_paise(series: list[int]) -> int | None:
    """The median of the most recent months -- this buyer's normal month.

    Median rather than mean on purpose: one exceptional month (a big one-off
    order, or a month where nothing landed) must not redefine what this buyer
    can normally absorb.
    """
    if not series:
        return None
    recent_months = int(rules()["score"]["ability"]["recent_months"])
    recent = series[-recent_months:] if recent_months > 0 else series
    return int(statistics.median(recent))


def outstanding_paise(invoice: dict[str, Any]) -> int:
    """What is still owed on one invoice, never negative."""
    amount = int(invoice.get("amount_paise") or 0)
    paid = int(invoice.get("amount_paid_paise") or 0)
    return max(0, amount - paid)


def ability(buyer: dict[str, Any], *, invoice_paise: int | None = None) -> dict[str, Any]:
    """How able is this buyer to pay -- 0-100, with the arithmetic.

    Args:
        buyer: the buyer record, carrying monthly_inflow_paise and
            failed_payment_count.
        invoice_paise: the amount still outstanding on the SPECIFIC invoice
            being judged, if there is one. Omitted, the score answers "can
            this buyer generally pay?"; supplied, it answers the sharper
            question "can this buyer pay THIS?" -- the same invoice is
            routine for a corporate and impossible for a small trader.

    Returns:
        score, the raw signals, and the breakdown that produced it.
    """
    config = rules()["score"]["ability"]
    weights = config["weights"]
    base = float(config["base"])
    low, high = float(config["min"]), float(config["max"])
    volatility_floor = float(config["volatility_floor_pct"])

    series = inflow_series(buyer)
    failed = failed_payment_count(buyer)
    trend_pct = inflow_trend_pct(series)
    volatility_pct = inflow_volatility_pct(series)
    typical = typical_monthly_inflow_paise(series)
    ratio = None
    if invoice_paise is not None and typical:
        ratio = round(invoice_paise / typical, 2)

    raw: dict[str, Any] = {
        "months_of_inflow": len(series),
        "inflow_trend_pct": trend_pct,
        "inflow_volatility_pct": volatility_pct,
        "typical_monthly_inflow_paise": typical,
        "failed_payment_count": failed,
        "invoice_to_capacity_ratio": ratio,
    }

    breakdown = [{"factor": "starting point", "detail": "neither able nor unable until the signals say so",
                  "points": base}]
    terms: list[tuple[str, str, float]] = []

    if not series:
        breakdown.append({
            "factor": "no inflow data",
            "detail": "no transaction history for this buyer; this is the neutral default, not a clean bill of health",
            "points": 0.0,
        })
    else:
        if trend_pct is None:
            breakdown.append({
                "factor": "inflow trend",
                "detail": f"only {len(series)} month(s) of inflow; not enough to call a trend",
                "points": 0.0,
            })
        else:
            direction = "up" if trend_pct >= 0 else "down"
            terms.append((
                "inflow trend",
                f"monthly inflow {direction} {abs(trend_pct)}% across {len(series)} months",
                trend_pct * float(weights["inflow_trend_pct"]),
            ))

        if volatility_pct is not None and volatility_pct > volatility_floor:
            terms.append((
                "inflow volatility",
                f"month-to-month swing of {volatility_pct}%, above the {volatility_floor}% noise floor",
                -(volatility_pct - volatility_floor) * float(weights["inflow_volatility_pct"]),
            ))

    if failed:
        terms.append((
            "failed payments",
            f"{failed} payment attempt(s) failed or bounced",
            -failed * float(weights["failed_payment"]),
        ))

    if ratio is not None:
        terms.append((
            "invoice vs capacity",
            f"this invoice is {ratio}x a typical month's inflow",
            -ratio * float(weights["invoice_to_capacity_ratio"]),
        ))
    elif invoice_paise is not None:
        breakdown.append({
            "factor": "invoice vs capacity",
            "detail": "no inflow history to size this invoice against",
            "points": 0.0,
        })

    breakdown += [
        {"factor": factor, "detail": detail, "points": round(points, 1)}
        for factor, detail, points in terms
    ]

    unclamped = base + sum(points for _factor, _detail, points in terms)
    result = _clamp(unclamped, low, high)
    if unclamped < low or unclamped > high:
        breakdown.append({
            "factor": "clamped",
            "detail": f"raw score {round(unclamped, 1)} pulled inside {int(low)}-{int(high)}",
            "points": round(result - unclamped, 1),
        })

    return {"score": result, "signals": raw, "breakdown": breakdown}


def ability_for_invoice(
    buyer: dict[str, Any],
    invoice: dict[str, Any],
) -> dict[str, Any]:
    """Ability to pay ONE specific invoice -- ability() sized to what is owed."""
    return ability(buyer, invoice_paise=outstanding_paise(invoice))


def willingness(history: list[dict[str, Any]]) -> dict[str, Any]:
    """How willing is this buyer to pay -- 0-100, with the arithmetic.

    Reuses engine.score.signals() rather than recomputing: this axis is a
    relabel of what the legacy formula already measures, not new evidence.
    With the shipped config its weights match score.weights exactly, so this
    equals the legacy score for the same history -- deliberate, and pinned by
    a test, so any future divergence is a decision somebody made rather than
    a drift nobody noticed.

    Args:
        history: the buyer's SETTLED invoices, newest first -- exactly what
            engine.score.settled_history() returns.
    """
    config = rules()["score"]["willingness"]
    weights = config["weights"]
    base = float(config["base"])
    low, high = float(config["min"]), float(config["max"])

    raw = score_engine.signals(history)
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

    breakdown = [{"factor": "starting point", "detail": "good faith until the record says otherwise",
                  "points": base}]
    breakdown += [
        {"factor": factor, "detail": detail, "points": round(points, 1)}
        for factor, detail, points in terms
    ]

    unclamped = base + sum(points for _factor, _detail, points in terms)
    result = _clamp(unclamped, low, high)
    if unclamped < low or unclamped > high:
        breakdown.append({
            "factor": "clamped",
            "detail": f"raw score {round(unclamped, 1)} pulled inside {int(low)}-{int(high)}",
            "points": round(result - unclamped, 1),
        })
    if not history:
        breakdown.append({
            "factor": "no history",
            "detail": "no settled invoices yet; this is the neutral default, not a good record",
            "points": 0.0,
        })

    return {"score": result, "signals": raw, "breakdown": breakdown}


def quadrant(ability_score: int, willingness_score: int) -> str:
    """Place a buyer in the 2x2. Pure function of two numbers and config.

    Boundaries are `score.quadrant` in config/rules.yaml, NOT score.bands --
    see that block's comment for why a three-way pacing split's edges are the
    wrong thing to borrow for a two-way one. "High" is at or above the
    boundary, so the boundary value itself counts as high on both axes.
    """
    config = rules()["score"]["quadrant"]
    able = ability_score >= int(config["ability_high_from"])
    willing = willingness_score >= int(config["willingness_high_from"])
    if able and willing:
        return GOOD_CUSTOMER
    if able:
        return CAN_PAY_BUT_WONT
    if willing:
        return CASH_FLOW_PROBLEM
    return HIGH_RISK


def two_axis_score(
    buyer: dict[str, Any],
    invoices: list[dict[str, Any]],
    today: date,
    *,
    invoice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The legacy score record, plus the two axes and the quadrant.

    Deliberately a separate function rather than extra keys on
    engine.score.score_buyer(): every existing caller (brain, writer,
    watchdog, buyer_panel, the simulator) reads that record's exact shape,
    and one of its tests pins the key set. Adding to it would have made this
    phase a change to the thing it promised not to touch. This composes on
    top instead, so the legacy record stays byte-for-byte what it was.

    Args:
        buyer: the buyer record.
        invoices: that buyer's invoices; unsettled ones are ignored for
            willingness, exactly as score_buyer() ignores them.
        today: the simulation clock.
        invoice: the specific invoice being judged, if there is one. Sharpens
            ability with the invoice-to-capacity ratio; omit for a
            buyer-level view.

    Returns:
        Everything score_buyer() returns, unchanged, plus "ability",
        "willingness" and "quadrant".
    """
    scored = score_engine.score_buyer(buyer, invoices, today)
    history = score_engine.settled_history(invoices, today=today)

    invoice_paise = outstanding_paise(invoice) if invoice is not None else None
    ability_record = ability(buyer, invoice_paise=invoice_paise)
    willingness_record = willingness(history)

    scored["ability"] = ability_record
    scored["willingness"] = willingness_record
    scored["quadrant"] = quadrant(ability_record["score"], willingness_record["score"])
    return scored


def _explain_axis(scored: dict[str, Any], axis: str, question: str) -> str:
    """Shared shape for the two explain functions -- same layout as score.explain()."""
    record = scored[axis]
    lines = [
        f"{scored['buyer_id']} {scored['name']}",
        f"  {axis} {record['score']}/100  ({question})",
        f"  quadrant {scored['quadrant']}: {QUADRANT_MEANING[scored['quadrant']]}",
        "  how it was calculated:",
    ]
    for item in record["breakdown"]:
        points = item["points"]
        sign = "+" if points > 0 else ""
        lines.append(f"    {sign}{points:>7}  {item['factor']:<20} {item['detail']}")
    return "\n".join(lines)


def explain_ability(scored: dict[str, Any]) -> str:
    """Why is ability this number? A paragraph a human can argue with."""
    return _explain_axis(scored, "ability", "can they pay?")


def explain_willingness(scored: dict[str, Any]) -> str:
    """Why is willingness this number? A paragraph a human can argue with."""
    return _explain_axis(scored, "willingness", "will they pay?")


def main() -> int:
    from data import store

    from engine.money import enable_unicode_output, format_inr

    enable_unicode_output()
    parser = argparse.ArgumentParser(
        description="Score every buyer on ability and willingness to pay.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="simulation date (default: the dataset's simulation_start)")
    parser.add_argument("--explain", metavar="BUYER_ID", default=None,
                        help="print the full arithmetic for one buyer")
    args = parser.parse_args()

    if not store.dataset_exists():
        print(f"no dataset found -- {store.REGENERATE_HINT}")
        return 1

    buyers = store.load_buyers()
    grouped = store.invoices_by_buyer(store.load_invoices())
    today = args.as_of or _as_date(store.load_meta()["simulation_start"])
    scored = [two_axis_score(b, grouped.get(b["buyer_id"], []), today) for b in buyers]
    scored.sort(key=lambda s: (s["ability"]["score"] + s["willingness"]["score"], s["buyer_id"]))

    if args.explain:
        match = next((s for s in scored if s["buyer_id"] == args.explain), None)
        if match is None:
            print(f"no such buyer: {args.explain}")
            return 1
        print(explain_ability(match))
        print()
        print(explain_willingness(match))
        return 0

    print(f"ability and willingness as of {today.isoformat()}")
    print(f"  {'buyer':<9}{'able':>6}{'willing':>9}{'legacy':>8}{'typical/mo':>14}"
          f"{'failed':>8}  {'quadrant':<18}name")
    for item in scored:
        typical = item["ability"]["signals"]["typical_monthly_inflow_paise"]
        print(
            f"  {item['buyer_id']:<9}{item['ability']['score']:>6}"
            f"{item['willingness']['score']:>9}{item['score']:>8}"
            f"{(format_inr(typical) if typical else '-'):>14}"
            f"{item['ability']['signals']['failed_payment_count']:>8}  "
            f"{item['quadrant']:<18}{item['name']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
