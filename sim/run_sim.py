"""Simulator -- runs the agent (and the baseline) day by day over the test world.

The baseline is a dumb reminder bot: three fixed reminders, ten days apart,
the same plain message for everyone, no score, no law, no promise memory, no
dispute detection -- roughly what payment-link reminders do today. Both run
on the SAME invoices with the SAME seed, so the comparison is honest.

    python sim/run_sim.py --seed 42 --days 120
    python sim/run_sim.py --compare --seed 42 --days 120 --verbose

Three things this module exists to get right, carried over from earlier
sessions and not left to a comment:

  * LLM_MODE is forced to "mock" for the whole day-loop, in code, regardless
    of .env -- see _forced_mock_mode(). A batch of up to 120 x ~100 decisions
    must never place a live API call by accident. engine/llm.py's own
    --calibrate / --list-models remain the one place a spot-check against the
    real model happens; that is a separate process invocation this module
    never touches.
  * Real history and real promises are threaded from one simulated day to the
    next, per invoice -- brain.decide() is never called with history=[] here
    the way main.py's single-day pipeline still does.
  * A first contact on an invoice that was already overdue before the
    watchdog ever saw it is paced for that backlog (engine/brain.py); this
    module is what actually exercises that path across a real multi-day
    window instead of a single frozen day.

Every reaction is rolled on its own random.Random seeded from
(seed, invoice_id, day, tag) -- not one shared mutable stream -- so the same
buyer facing the same message on the same simulated day gets the same dice
roll whether this is the baseline run or the agent run. That is what makes
"same seed, same invoices" in ARCHITECTURE.md a real guarantee rather than an
accident of call order.
"""

from __future__ import annotations

import argparse
import copy
import os
import random
import sys
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import store
from engine import audit, brain, channels, law, llm, promises, watchdog, writer
from engine import score as score_engine
from engine.money import enable_unicode_output, format_inr
from sim import personas

DEFAULT_SEED = 42
DEFAULT_DAYS = 120

#: The dumb baseline: three fixed reminders, ten days apart, always rung 1.
BASELINE_MAX_MESSAGES = 3
BASELINE_INTERVAL_DAYS = 10
BASELINE_RUNG = 1


# --------------------------------------------------------------------------
# forcing mock mode -- see the module docstring
# --------------------------------------------------------------------------

@contextmanager
def _forced_mock_mode():
    """Every LLM call inside this block is canned, whatever .env says."""
    previous = os.environ.get("LLM_MODE")
    os.environ["LLM_MODE"] = llm.MOCK
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LLM_MODE", None)
        else:
            os.environ["LLM_MODE"] = previous


def _rng(seed: int, invoice_id: str, today: date, tag: str) -> random.Random:
    """A fresh, deterministic stream for one (invoice, day, purpose) triple.

    Independent of call order, so baseline and agent runs -- which send
    different numbers of messages on different days -- still give the same
    buyer the same underlying roll on the same simulated day.
    """
    return random.Random(f"{seed}|{invoice_id}|{today.toordinal()}|{tag}")


# --------------------------------------------------------------------------
# the fake world
# --------------------------------------------------------------------------

def _load_world() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], date]:
    """A fresh, independent copy of buyers/invoices/personas -- never shared."""
    if not store.dataset_exists():
        raise SystemExit(f"no dataset found -- {store.REGENERATE_HINT}")
    buyers = copy.deepcopy(store.load_buyers())
    invoices = copy.deepcopy(store.load_invoices())
    persona_of = personas.load_hidden_personas()
    today0 = date.fromisoformat(store.load_meta()["simulation_start"])
    return buyers, invoices, persona_of, today0


def _current(invoices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [inv for inv in invoices if inv.get("cohort") == "current"]


def _apply_payment(invoice: dict[str, Any], amount_paise: int, today: date) -> None:
    """Ground truth: money actually landed. Never more than what is owed."""
    remaining = law.outstanding_paise(invoice, today)
    amount_paise = max(0, min(int(amount_paise), remaining))
    if amount_paise <= 0:
        return
    invoice.setdefault("partial_payments", []).append(
        {"date": today.isoformat(), "amount_paise": amount_paise})
    invoice["amount_paid_paise"] = int(invoice.get("amount_paid_paise", 0)) + amount_paise
    if invoice["amount_paid_paise"] >= int(invoice["amount_paise"]):
        invoice["status"] = "paid"
        invoice["paid_date"] = today.isoformat()
    else:
        invoice["status"] = "partially_paid"


def _advance_promises(
    invoices: list[dict[str, Any]],
    promises_by_invoice: dict[str, list[dict[str, Any]]],
    persona_of: dict[str, str],
    today: date,
    seed: int,
    log: bool,
) -> None:
    """Resolve every promise maturing today, then sweep the rest for breaks.

    A promise is the BUYER's commitment, not the seller's bookkeeping -- it
    matures whether or not the caller is smart enough to reference it later,
    so this runs identically for the agent and the dumb baseline.
    """
    for invoice in invoices:
        plist = promises_by_invoice.get(invoice["invoice_id"])
        if not plist:
            continue
        persona = persona_of[invoice["buyer_id"]]
        for promise in plist:
            if promise["status"] != "open" or promise["promised_date"] != today.isoformat():
                continue
            rng = _rng(seed, invoice["invoice_id"], today, "keep")
            if not personas.keeps_promise(persona, rng):
                continue  # left open; sweep() below marks it broken tomorrow
            remaining = law.outstanding_paise(invoice, today)
            if promise.get("amount") == "partial":
                _apply_payment(invoice, int(remaining * rng.uniform(0.4, 0.7)), today)
            else:
                _apply_payment(invoice, remaining, today)
            promises.mark_kept(promise, today, log=log)
        promises.sweep(plist, today, log=log)


def _apply_reaction(
    invoice: dict[str, Any],
    plist: list[dict[str, Any]],
    reaction: dict[str, Any],
    today: date,
    seed: int,
    log: bool,
) -> str:
    """Apply what the persona did and return the history outcome tag."""
    outcome = reaction["outcome"]
    if outcome == personas.PAY_FULL:
        _apply_payment(invoice, law.outstanding_paise(invoice, today), today)
        return "paid_full"
    if outcome == personas.PAY_PARTIAL:
        rng = _rng(seed, invoice["invoice_id"], today, "partial_amount")
        remaining = law.outstanding_paise(invoice, today)
        _apply_payment(invoice, int(remaining * rng.uniform(0.35, 0.6)), today)
        # A part-payment with no explanation is exactly the ambiguous case
        # engine.brain._is_ambiguous looks for -- this is what exercises it.
        return "unclear_reply"
    if outcome in (personas.PROMISE, personas.DISPUTE):
        parsed = promises.parse_reply(
            reaction["reply"], today, variant=reaction["variant"],
            invoice_id=invoice["invoice_id"], buyer_id=invoice["buyer_id"], log=log,
        )
        promises.apply_reply(parsed, invoice, plist, today, log=log)
        return "promise_made" if outcome == personas.PROMISE else "disputed"
    return "no_reply"


def _totals(invoices: list[dict[str, Any]], today: date) -> dict[str, Any]:
    current = _current(invoices)
    disputed = [inv for inv in current if inv.get("disputed")]
    return {
        "day": today.isoformat(),
        "recovered_paise": sum(int(inv.get("amount_paid_paise", 0)) for inv in current),
        "outstanding_paise": sum(law.outstanding_paise(inv, today) for inv in current),
        "disputed_paise": sum(law.outstanding_paise(inv, today) for inv in disputed),
        "disputed_count": len(disputed),
    }


def verify_conservation(invoices: list[dict[str, Any]], as_of: date) -> None:
    """Money in must equal money accounted for. No invoice may leak or gain paise."""
    for invoice in _current(invoices):
        amount = int(invoice["amount_paise"])
        paid = int(invoice.get("amount_paid_paise", 0))
        itemised = sum(int(p["amount_paise"]) for p in invoice.get("partial_payments") or [])
        outstanding = law.outstanding_paise(invoice, as_of)
        assert paid == itemised, (
            f"{invoice['invoice_id']}: amount_paid_paise {paid} != itemised payments {itemised}")
        assert paid + outstanding == amount, (
            f"{invoice['invoice_id']}: paid {paid} + outstanding {outstanding} != amount {amount}")
        assert 0 <= paid <= amount, f"{invoice['invoice_id']}: paid {paid} out of bounds"


def _narrate(day_number: int, buyer: dict[str, Any], persona: str, rung: int, outcome: str,
            promise: dict[str, Any] | None = None) -> str:
    name = buyer.get("name", buyer.get("buyer_id"))
    verbs = {
        "paid_full": f"paid in full after the rung-{rung} message",
        "unclear_reply": f"paid part of it after the rung-{rung} message, with no explanation",
        "promise_made": (f"promised to pay by {promise['promised_date']} after the "
                         f"rung-{rung} message" if promise else f"promised to pay after rung-{rung}"),
        "disputed": f"disputed the invoice after the rung-{rung} message",
        "no_reply": f"ignored the rung-{rung} message",
    }
    return f"Day {day_number}: {name} ({persona}) {verbs.get(outcome, outcome)}"


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------

def run_agent(seed: int, days: int, verbose: bool = False) -> dict[str, Any]:
    """Run the full agent (watchdog -> score -> law -> brain -> writer ->
    channels -> persona reacts -> promises) over `days` simulated days.

    This is the one place in the whole system that produces a full,
    reproducible audit trail: the log is cleared at the start (a fresh run
    should leave a fresh, self-consistent trail for the seed and window it
    was asked for) and every decision, draft and delivery is written with
    log=True, exactly as production would.
    """
    buyers, invoices, persona_of, day0 = _load_world()
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    history: dict[str, list[dict[str, Any]]] = {}
    promises_by_invoice: dict[str, list[dict[str, Any]]] = {}
    seen_rungs: dict[str, set[int]] = {}
    announced: set[str] = set()
    handoffs: set[str] = set()
    stops: set[str] = set()
    disputes: set[str] = set()
    messages_sent = 0
    narrative: list[str] = []
    daily: list[dict[str, Any]] = []

    audit.clear()
    audit.enable()

    with _forced_mock_mode():
        for offset in range(days):
            today = day0 + timedelta(days=offset)

            _advance_promises(invoices, promises_by_invoice, persona_of, today, seed, log=True)

            queue = watchdog.overdue_invoices(invoices, today)
            grouped = store.invoices_by_buyer(invoices)
            scores = {s["buyer_id"]: s for s in score_engine.score_all(buyers, grouped, today)}

            for invoice in queue:
                inv_id = invoice["invoice_id"]
                buyer = buyers_by_id[invoice["buyer_id"]]
                persona = persona_of[buyer["buyer_id"]]
                position = law.legal_position(invoice, today)
                plist = promises_by_invoice.setdefault(inv_id, [])
                hist = history.setdefault(inv_id, [])

                action = brain.decide(invoice, buyer, scores[buyer["buyer_id"]], position,
                                      promises=plist, history=hist, log=True)

                if action.kind == brain.SEND and action.skeleton:
                    drafted = writer.write_message(
                        action.skeleton, invoice=invoice, buyer=buyer,
                        score=scores[buyer["buyer_id"]], promises=plist, today=today, log=True,
                    )
                    target = buyer.get("preferred_channel", "email")
                    to = buyer.get("contact_email") if target == "email" else buyer.get("contact_phone", "")
                    channels.send(target, to, drafted, invoice_id=inv_id, buyer_id=buyer["buyer_id"],
                                 rung=action.rung, today=today, enabled=False, log=True)
                    messages_sent += 1

                    rng = _rng(seed, inv_id, today, "react")
                    reaction = personas.react(persona, action.rung, rng)
                    outcome = _apply_reaction(invoice, plist, reaction, today, seed, log=True)
                    hist.append({"date": today.isoformat(), "rung": action.rung,
                                "channel": target, "outcome": outcome})

                    seen = seen_rungs.setdefault(inv_id, set())
                    newly_seen_rung = action.rung not in seen
                    seen.add(action.rung)
                    if verbose and (newly_seen_rung or outcome in ("paid_full", "disputed")):
                        promise = plist[-1] if outcome == "promise_made" and plist else None
                        narrative.append(_narrate(offset + 1, buyer, persona, action.rung,
                                                  outcome, promise))
                    if outcome == "disputed":
                        disputes.add(inv_id)

                elif action.kind in (brain.HANDOFF, brain.STOP) and inv_id not in announced:
                    announced.add(inv_id)
                    (handoffs if action.kind == brain.HANDOFF else stops).add(inv_id)
                    if verbose:
                        narrative.append(f"Day {offset + 1}: {buyer['name']} ({persona}) "
                                         f"{action.kind} -- {action.reason}")

            daily.append(_totals(invoices, today))

    verify_conservation(invoices, day0 + timedelta(days=days - 1))
    return {
        "mode": "agent", "seed": seed, "days": days,
        "final": daily[-1] if daily else _totals(invoices, day0),
        "messages_sent": messages_sent,
        "handoffs": len(handoffs), "stops": len(stops), "disputes": len(disputes),
        "not_recovered": [inv["invoice_id"] for inv in _current(invoices)
                          if inv.get("status") not in ("paid",)],
        "daily": daily, "narrative": narrative,
    }


# --------------------------------------------------------------------------
# the baseline
# --------------------------------------------------------------------------

def _generic_reminder(invoice: dict[str, Any]) -> dict[str, str]:
    """The one message the baseline ever sends -- same words for everyone."""
    return {
        "subject": f"Payment reminder -- invoice {invoice['invoice_id']}",
        "body": (f"Dear Sir/Madam, this is a reminder that invoice "
                f"{invoice['invoice_id']} for {format_inr(invoice['amount_paise'])} "
                f"is still outstanding. Please arrange payment at your earliest convenience."),
    }


def run_baseline(seed: int, days: int, verbose: bool = False) -> dict[str, Any]:
    """Three fixed reminders, ten days apart, the same message for everyone.

    No score, no law, no rung, no dispute detection -- a dumb bot does not
    know it has been disputed, so it keeps sending until it hits its cap.
    Promises are still tracked and still mature (a buyer's commitment is real
    whether or not this bot is smart enough to reference it), which is what
    makes the comparison against the agent honest rather than stacked.
    """
    buyers, invoices, persona_of, day0 = _load_world()
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    promises_by_invoice: dict[str, list[dict[str, Any]]] = {}
    sent_count: dict[str, int] = {}
    last_sent: dict[str, date] = {}
    disputes: set[str] = set()
    messages_sent = 0
    narrative: list[str] = []
    daily: list[dict[str, Any]] = []

    with _forced_mock_mode():
        for offset in range(days):
            today = day0 + timedelta(days=offset)

            _advance_promises(invoices, promises_by_invoice, persona_of, today, seed, log=False)

            for invoice in watchdog.overdue_invoices(invoices, today):
                inv_id = invoice["invoice_id"]
                sent = sent_count.get(inv_id, 0)
                if sent >= BASELINE_MAX_MESSAGES:
                    continue
                last = last_sent.get(inv_id)
                if last is not None and (today - last).days < BASELINE_INTERVAL_DAYS:
                    continue

                buyer = buyers_by_id[invoice["buyer_id"]]
                persona = persona_of[buyer["buyer_id"]]
                message = _generic_reminder(invoice)
                target = buyer.get("preferred_channel", "email")
                to = buyer.get("contact_email") if target == "email" else buyer.get("contact_phone", "")
                channels.send(target, to, message, invoice_id=inv_id, buyer_id=buyer["buyer_id"],
                             rung=BASELINE_RUNG, today=today, enabled=False, log=False)
                messages_sent += 1
                sent_count[inv_id] = sent + 1
                last_sent[inv_id] = today

                rng = _rng(seed, inv_id, today, "baseline_react")
                reaction = personas.react(persona, BASELINE_RUNG, rng)
                plist = promises_by_invoice.setdefault(inv_id, [])
                outcome = _apply_reaction(invoice, plist, reaction, today, seed, log=False)
                if outcome == "disputed":
                    disputes.add(inv_id)
                if verbose:
                    narrative.append(
                        f"Day {offset + 1}: [baseline] {buyer['name']} ({persona}) "
                        f"reminder {sent_count[inv_id]}/{BASELINE_MAX_MESSAGES} -> {outcome}")

            daily.append(_totals(invoices, today))

    verify_conservation(invoices, day0 + timedelta(days=days - 1))
    return {
        "mode": "baseline", "seed": seed, "days": days,
        "final": daily[-1] if daily else _totals(invoices, day0),
        "messages_sent": messages_sent,
        "handoffs": 0, "stops": 0, "disputes": len(disputes),
        "not_recovered": [inv["invoice_id"] for inv in _current(invoices)
                          if inv.get("status") not in ("paid",)],
        "daily": daily, "narrative": narrative,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_summary(label: str, report: dict[str, Any]) -> None:
    final = report["final"]
    print(f"{label}")
    print(f"  recovered            {format_inr(final['recovered_paise'], 'Rs '):>16}")
    print(f"  outstanding          {format_inr(final['outstanding_paise'], 'Rs '):>16}")
    print(f"  of which disputed    {format_inr(final['disputed_paise'], 'Rs '):>16} "
          f"({final['disputed_count']} invoices)")
    print(f"  messages sent        {report['messages_sent']:>16}")
    print(f"  escalated to human   {report['handoffs']:>16}")
    print(f"  hard-stopped         {report['stops']:>16}")
    print(f"  not recovered        {len(report['not_recovered']):>16}")


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(description="Run the recovery simulation.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: 42)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="simulated days to run")
    parser.add_argument("--compare", action="store_true", help="run baseline and agent side by side")
    parser.add_argument("--verbose", action="store_true", help="print a daily narrative")
    args = parser.parse_args()

    if not store.dataset_exists():
        print(f"no dataset found -- {store.REGENERATE_HINT}")
        return 1

    print(f"simulator: seed={args.seed}, days={args.days}, "
          f"mode={'baseline vs agent' if args.compare else 'agent only'}")

    if args.compare:
        baseline = run_baseline(args.seed, args.days, verbose=args.verbose)
        agent = run_agent(args.seed, args.days, verbose=args.verbose)
        if args.verbose:
            print()
            print("-- baseline narrative --")
            for line in baseline["narrative"]:
                print(f"  {line}")
            print()
            print("-- agent narrative --")
            for line in agent["narrative"]:
                print(f"  {line}")
        print()
        _print_summary("baseline", baseline)
        print()
        _print_summary("agent", agent)
        gain = agent["final"]["recovered_paise"] - baseline["final"]["recovered_paise"]
        print()
        print(f"agent recovered {format_inr(gain, 'Rs ')} more than the baseline "
              f"with {agent['messages_sent'] - baseline['messages_sent']:+d} messages")
    else:
        agent = run_agent(args.seed, args.days, verbose=args.verbose)
        if args.verbose:
            print()
            for line in agent["narrative"]:
                print(f"  {line}")
        print()
        _print_summary("agent", agent)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
