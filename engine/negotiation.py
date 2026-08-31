"""Recovery probability + expected value -- which action is worth taking?

Rules, never AI, exactly like engine/score.py and engine/ability_willingness.py:
every number here carries the arithmetic that produced it and every weight
comes from config/rules.yaml.

engine/ability_willingness.py answers "can this buyer pay, and will they?"
and places them in one of four quadrants. This module answers the next
question: for a buyer in that quadrant, which of a fixed set of candidate
recovery actions is actually worth taking?

    P(recover)              engine/config's negotiation.recovery_probability,
                             per (quadrant, action) -- an assumption, not a
                             measured outcome (see below for why this is a
                             flat grid rather than a weighted formula).
    expected_recovery_paise  outstanding_paise, scaled by how much of it
                             recovery_fraction says this action collects
                             WHEN it succeeds (full value for a message or a
                             payment plan; a partial settlement for
                             counter_settle / human_handoff / legal_escalation).
    cost_paise               what taking this action costs us: an LLM draft
                             call for a message action, or minutes of the
                             MSME owner's own time for a handoff.
    ev_paise                 round(probability/100 * expected_recovery_paise)
                             - cost_paise.

The action space, named the same way engine/brain.py names its rungs
(WAIT, SEND, HANDOFF, STOP) but as its OWN constants -- this module imports
NOTHING from engine.brain, and never will at module scope: Phase 3 has
engine/brain.py import THIS module, and a two-way import would be a cycle.
tests/test_negotiation.py's no-cycle guard AST-walks this file's top-level
imports to keep that true forever, not just today.

    WAIT SOFT_NUDGE FIRM LEGAL_FACTS   -- share names with brain.py's rungs
                                          0-3 on purpose: same real-world
                                          action, named the same way, so
                                          Phase 3 can line them up.
    PAYMENT_PLAN COUNTER_SETTLE        -- genuinely new: a buyer proposing
                                          "70% now, waive the rest" is
                                          COUNTER_SETTLE; a schedule for the
                                          full amount is PAYMENT_PLAN.
    HUMAN_HANDOFF LEGAL_ESCALATION     -- both correspond to today's rung 4
                                          (a human takes over) but are
                                          scored separately: a phone call and
                                          the Samadhaan reference path
                                          (engine/samadhaan.py) have
                                          different costs and different odds.

KNOWN, DOCUMENTED SIMPLIFICATION: probability is scored per (quadrant,
action) only -- not by current rung, and not by how many contacts have
already happened. A buyer who has ignored three firm messages plausibly has
a different soft_nudge probability than one on their first contact, but a
third dimension with no real data to calibrate it against would be
decoration, not accuracy. A candidate for Phase 3 refinement, not built here.

WHY A FLAT GRID, NOT A WEIGHTED FORMULA (the design call this phase's brief
left open): ability()/willingness() decompose into weighted per-signal terms
because those terms have plausible units -- percent inflow decline maps to
score points in a way a reader can sanity-check. A "probability weight" here
would have no such unit: there is no measured recovery-rate data behind any
of these numbers, so dressing a guess up as arithmetic (weights, terms,
partial credit) would be LESS honest than a flat, visibly-a-guess grid, not
more. This confirms this module's own brief's lean.

A SURPRISING RESULT WORTH STATING RATHER THAN QUIETLY FIXING: with the
shipped grid, rank_actions() puts legal_facts (or legal_escalation) ABOVE
soft_nudge even for a good_customer -- the buyer who pays best. The model has
no term for the relationship cost of over-escalating a good payer, only
P(recover), and more assertive contact is modelled as always at least as
likely to work, at essentially the same near-zero LLM cost as a gentle one.
This is exactly the kind of thing shipping the reasoning inert first is FOR:
the number is visible and arguable before it is allowed to move money. Not
patched here by hand-tuning good_customer's row to produce a different
answer -- this phase's required sanity checks are about cash_flow_problem and
can_pay_but_wont (see tests/test_negotiation.py), and quietly retuning an
unrelated row to hide an honest result would be worse than reporting it.
Candidate fix for Phase 3: either a relationship-cost term, or having the
brain restrict candidate actions to the buyer's current rung and neighbours
rather than letting raw EV jump straight to the most assertive option.

MONEY-SAFETY NOTE, worth repeating in code as well as in the phase brief:
every number this module produces is advisory arithmetic over a
HYPOTHETICAL action, not a real transaction. ev_paise must never be written
to an invoice's amount_paid_paise or otherwise touch real ledger state -- it
is evaluated for comparison only, in the same spirit as engine/law.py's
section_16_running "cost of waiting" figure, which projects forward without
mutating anything.

PHASE 2 SCOPE, stated plainly: this module is computed and ranked, and
nothing acts on it. engine/brain.py does not import it, Action.kind stays
exactly the four strings it is today. Wiring a chosen action into the brain
is Phase 3.

    python engine/negotiation.py --explain INVOICE_ID
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it, by
# putting the repo root on the path when there is no enclosing package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import ability_willingness as aw
from engine.config import rules
from engine.law import _as_date
from engine.money import round_paise

#: The action space. Shares names with engine.brain.py's rungs 0-3 on
#: purpose (see module docstring) but is defined independently -- this
#: module has zero dependency on engine.brain.
WAIT, SOFT_NUDGE, FIRM, LEGAL_FACTS = "wait", "soft_nudge", "firm", "legal_facts"
PAYMENT_PLAN, COUNTER_SETTLE = "payment_plan", "counter_settle"
HUMAN_HANDOFF, LEGAL_ESCALATION = "human_handoff", "legal_escalation"

ACTIONS: tuple[str, ...] = (
    WAIT, SOFT_NUDGE, FIRM, LEGAL_FACTS,
    PAYMENT_PLAN, COUNTER_SETTLE, HUMAN_HANDOFF, LEGAL_ESCALATION,
)

#: Actions that produce one drafted message -- costed identically, at the
#: draft_message ceiling, since each is one call to engine.llm.
_MESSAGE_ACTIONS = frozenset({SOFT_NUDGE, FIRM, LEGAL_FACTS, PAYMENT_PLAN, COUNTER_SETTLE})


def _clamp_pct(value: float) -> int:
    return int(round(min(100.0, max(0.0, value))))


# --------------------------------------------------------------------------
# the three inputs to EV
# --------------------------------------------------------------------------

def recovery_probability(
    quadrant: str, action: str, *, broken_promises: int = 0,
) -> dict[str, Any]:
    """P(recover) for this action if taken now, 0-100, with the arithmetic."""
    if quadrant not in aw.QUADRANTS:
        raise ValueError(f"unknown quadrant {quadrant!r}; expected one of {aw.QUADRANTS}")
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")

    config = rules()["negotiation"]
    base = float(config["recovery_probability"][quadrant][action])
    adjustment = config["promise_adjustment"]

    breakdown: list[dict[str, Any]] = [{
        "factor": "base rate",
        "detail": f"assumed P(recover) for {action} against a {quadrant} buyer "
                  f"({aw.QUADRANT_MEANING[quadrant]})",
        "points": base,
    }]

    penalty = 0.0
    if broken_promises and action in adjustment["applies_to"]:
        per_promise = float(adjustment["penalty_per_broken_promise"])
        penalty = broken_promises * per_promise
        breakdown.append({
            "factor": "broken promises",
            "detail": f"{broken_promises} broken promise(s) x {per_promise} point(s) off -- "
                      f"{action} depends on the buyer's own follow-through",
            "points": -penalty,
        })

    unclamped = base - penalty
    probability = _clamp_pct(unclamped)
    if unclamped < 0 or unclamped > 100:
        breakdown.append({
            "factor": "clamped",
            "detail": f"raw {round(unclamped, 1)} pulled inside 0-100",
            "points": round(probability - unclamped, 1),
        })

    return {"probability": probability, "breakdown": breakdown}


def recovery_fraction(action: str) -> float:
    """Fraction of outstanding recovered when the action succeeds. Config passthrough."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")
    return float(rules()["negotiation"]["recovery_fraction"][action])


def expected_recovery_paise(action: str, outstanding_paise: int) -> int:
    """outstanding_paise * recovery_fraction(action), rounded."""
    return round_paise(outstanding_paise * recovery_fraction(action))


def action_cost_paise(action: str) -> dict[str, Any]:
    """{cost_paise, breakdown} -- cites where the number came from.

    wait costs nothing. Every message action (soft_nudge, firm, legal_facts,
    payment_plan, counter_settle) costs one drafted message, at the
    draft_message ceiling -- computed here from config, never duplicated as
    its own number. human_handoff and legal_escalation cost minutes of the
    MSME owner's own time, at different fixed durations.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; expected one of {ACTIONS}")

    config = rules()["negotiation"]["cost"]

    if action == WAIT:
        return {
            "cost_paise": 0,
            "breakdown": [{"factor": "no action taken", "detail": "waiting costs nothing", "points": 0}],
        }

    if action in _MESSAGE_ACTIONS:
        cost = int(config["llm_call_paise"]["draft_message"])
        max_tokens = int(rules()["llm"]["max_tokens"]["draft_message"])
        return {
            "cost_paise": cost,
            "breakdown": [{
                "factor": "drafted message",
                "detail": f"one LLM call at the draft_message ceiling ({max_tokens} tokens)",
                "points": cost,
            }],
        }

    minutes_key = "human_handoff_minutes" if action == HUMAN_HANDOFF else "legal_escalation_minutes"
    minutes = int(config[minutes_key])
    per_minute = int(config["human_minute_paise"])
    cost = minutes * per_minute
    return {
        "cost_paise": cost,
        "breakdown": [{
            "factor": "human time",
            "detail": f"{minutes} minute(s) of the owner's own time at {per_minute} paise/minute",
            "points": cost,
        }],
    }


# --------------------------------------------------------------------------
# composing the verdict
# --------------------------------------------------------------------------

def evaluate_action(
    action: str, *, quadrant: str, outstanding_paise: int, broken_promises: int = 0,
) -> dict[str, Any]:
    """One action's full EV verdict.

    ev_paise = round(probability/100 * expected_recovery_paise) - cost_paise.
    The breakdown's points sum EXACTLY to ev_paise -- the same guarantee
    every rule module in this codebase gives, made trivial here by keeping
    the probability and fraction as zero-point context entries and the two
    real money terms (expected recovery, cost) as the only ones that count.
    """
    probability_record = recovery_probability(quadrant, action, broken_promises=broken_promises)
    probability = probability_record["probability"]
    fraction = recovery_fraction(action)
    expected_recovery = expected_recovery_paise(action, outstanding_paise)
    cost_record = action_cost_paise(action)
    cost_paise = cost_record["cost_paise"]

    gross_paise = round_paise(probability / 100 * expected_recovery)
    ev_paise = gross_paise - cost_paise

    breakdown: list[dict[str, Any]] = [
        {
            "factor": "probability",
            "detail": f"{probability}% chance of recovering via {action} for a {quadrant} buyer",
            "points": 0,
        },
        {
            "factor": "recovery fraction",
            "detail": f"{action} recovers {fraction * 100:.0f}% of the {outstanding_paise} paise "
                      f"outstanding if it succeeds ({expected_recovery} paise)",
            "points": 0,
        },
        {
            "factor": "expected recovery value",
            "detail": f"{probability}% of {expected_recovery} paise",
            "points": gross_paise,
        },
        {
            "factor": "cost",
            "detail": cost_record["breakdown"][0]["detail"],
            "points": -cost_paise,
        },
    ]

    return {
        "action": action,
        "probability": probability,
        "expected_recovery_paise": expected_recovery,
        "cost_paise": cost_paise,
        "ev_paise": ev_paise,
        "breakdown": breakdown,
        "probability_breakdown": probability_record["breakdown"],
        "cost_breakdown": cost_record["breakdown"],
    }


def rank_actions(
    quadrant: str,
    outstanding_paise: int,
    *,
    broken_promises: int = 0,
    candidates: tuple[str, ...] = ACTIONS,
) -> list[dict[str, Any]]:
    """Every candidate action's evaluate_action() result, best ev_paise first.

    Ties broken by action name, so the ranking is deterministic even when
    two actions land on the exact same EV.
    """
    results = [
        evaluate_action(action, quadrant=quadrant, outstanding_paise=outstanding_paise,
                        broken_promises=broken_promises)
        for action in candidates
    ]
    results.sort(key=lambda result: (-result["ev_paise"], result["action"]))
    return results


def evaluate_invoice(
    buyer: dict[str, Any],
    invoice: dict[str, Any],
    invoices: list[dict[str, Any]],
    today: date,
    *,
    broken_promises: int,
) -> dict[str, Any]:
    """Everything two_axis_score() returns for this invoice, plus a ranking.

    Args:
        buyer: the buyer record.
        invoice: the specific invoice being judged.
        invoices: that buyer's invoices, for two_axis_score()'s own history.
        today: the simulation clock.
        broken_promises: REQUIRED, not defaulted -- this module has no
            promise data of its own (engine.promises / engine.brain own
            that), so the caller must supply it explicitly rather than this
            function silently assuming 0 and quietly being wrong for every
            buyer with a real promise history.

    Returns:
        The two_axis_score() record (score, ability, willingness, quadrant,
        ...) plus "actions": rank_actions()'s output for this invoice's
        outstanding amount and quadrant.
    """
    scored = aw.two_axis_score(buyer, invoices, today, invoice=invoice)
    outstanding = aw.outstanding_paise(invoice)
    actions = rank_actions(scored["quadrant"], outstanding, broken_promises=broken_promises)
    return {**scored, "actions": actions}


def explain_action(result: dict[str, Any]) -> str:
    """Why this EV number, in the same plain-English layout as
    ability_willingness.py's _explain_axis()."""
    lines = [
        f"{result['action']}",
        f"  P(recover) {result['probability']}%   "
        f"expected recovery {result['expected_recovery_paise']} paise   "
        f"cost {result['cost_paise']} paise   ->   EV {result['ev_paise']} paise",
        "  how it was calculated:",
    ]
    for item in result["breakdown"]:
        points = item["points"]
        sign = "+" if points > 0 else ""
        lines.append(f"    {sign}{points:>10}  {item['factor']:<24} {item['detail']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    from data import store

    from engine.money import enable_unicode_output, format_inr

    enable_unicode_output()
    parser = argparse.ArgumentParser(
        description="Rank recovery actions by expected value for one invoice.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="simulation date (default: the dataset's simulation_start)")
    parser.add_argument("--explain", metavar="INVOICE_ID", required=True,
                        help="print the EV ranking for one invoice")
    parser.add_argument("--broken-promises", type=int, default=None,
                        help="override the broken-promise count used in the ranking. No "
                             "promise store is persisted to disk for a standalone invoice "
                             "lookup (promises live only in-memory during a live run or "
                             "simulation) -- omit this to use 0, the honest default.")
    args = parser.parse_args()

    if not store.dataset_exists():
        print(f"no dataset found -- {store.REGENERATE_HINT}")
        return 1

    buyers = {b["buyer_id"]: b for b in store.load_buyers()}
    invoices = store.load_invoices()
    grouped = store.invoices_by_buyer(invoices)
    today = args.as_of or _as_date(store.load_meta()["simulation_start"])

    invoice = next((inv for inv in invoices if inv["invoice_id"] == args.explain), None)
    if invoice is None:
        print(f"no such invoice: {args.explain}")
        return 1
    buyer = buyers.get(invoice["buyer_id"])
    if buyer is None:
        print(f"invoice {args.explain} has no matching buyer record")
        return 1

    if args.broken_promises is not None:
        broken = args.broken_promises
    else:
        # Local import, deliberately not at module scope: engine.brain will
        # import THIS module in Phase 3, and a top-level import here would
        # make that a cycle. There is no persisted promise store to read for
        # a standalone lookup, so this always resolves to 0 today -- kept
        # for the day a real promise store exists for this CLI to read.
        from engine.brain import broken_promises as brain_broken_promises

        grace = int(rules()["ladder"].get("promise_grace_days", 0))
        broken = brain_broken_promises([], today, grace)

    result = evaluate_invoice(buyer, invoice, grouped.get(buyer["buyer_id"], []), today,
                              broken_promises=broken)

    print(f"{invoice['invoice_id']}  buyer {buyer['buyer_id']} {buyer.get('name')}  "
          f"quadrant {result['quadrant']} ({aw.QUADRANT_MEANING[result['quadrant']]})")
    print(f"outstanding: {format_inr(aw.outstanding_paise(invoice))}"
          f"   broken promises considered: {broken}")
    print()
    print(f"  {'action':<18}{'EV':>16}{'P(recover)':>12}{'cost':>14}")
    for action in result["actions"]:
        print(f"  {action['action']:<18}{format_inr(action['ev_paise']):>16}"
              f"{action['probability']:>11}%"
              f"{format_inr(action['cost_paise'], decimals=True):>14}")
    print()
    print(explain_action(result["actions"][0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
