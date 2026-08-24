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
import json
import os
import random
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import generate, store
from engine import audit, brain, channels, law, llm, promises, watchdog, writer
from engine import score as score_engine
from engine.money import enable_unicode_output, format_inr
from sim import personas

DEFAULT_SEED = 42
DEFAULT_DAYS = 120

#: The dumb baseline: three fixed reminders, evenly spaced, always the same
#: plain message, no score, no law, no promise memory, no dispute detection.
#: Confirmed against Razorpay's own Payment Links reminders (web search,
#: 2026-08-24): capped at 3 reminders, scheduled off the link's expiry/issue
#: date rather than any buyer behaviour, no personalisation documented.
#:   https://razorpay.com/docs/payments/payment-links/reminders/
#:   https://razorpay.com/docs/api/payments/payment-links/reminders/
#: The 10-day spacing is our own choice, not Razorpay's (they don't publish
#: one universal default -- it's merchant-configurable).
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

def _load_world(seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], date]:
    """A fresh, independent copy of buyers/invoices/personas -- never shared.

    Regenerates on disk first if it is missing or was built for a different
    seed (data.generate.ensure_dataset) -- otherwise a run asked for --seed 7
    could silently replay whatever seed happened to be on disk already.
    """
    generate.ensure_dataset(seed)
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


#: How a HANDOFF/STOP reason (engine.brain's own sentence) is bucketed for
#: reporting. Matched by substring against the exact phrasing brain.decide()
#: writes -- if that wording ever changes these buckets need a look too.
_REASON_BUCKETS: tuple[tuple[str, str], ...] = (
    ("disputed", "disputed"),
    ("opted out", "opted_out"),
    ("escalated to the final rung", "rung4_escalation"),
    ("reaching the limit", "max_contacts_reached"),
)


def _classify_reason(reason: str) -> str:
    for needle, bucket in _REASON_BUCKETS:
        if needle in reason:
            return bucket
    return "other"


def paid_days_map(invoices: list[dict[str, Any]]) -> dict[str, int]:
    """invoice_id -> days from issue to payment, for every current invoice paid."""
    return {
        inv["invoice_id"]: (date.fromisoformat(inv["paid_date"])
                            - date.fromisoformat(inv["issue_date"])).days
        for inv in _current(invoices)
        if inv.get("status") == "paid" and inv.get("paid_date")
    }


def avg_days_to_pay(invoices: list[dict[str, Any]]) -> float | None:
    """Mean days from issue to payment, over current invoices actually paid.

    Caution when comparing this across two different agents: it is an average
    over whatever each one happened to recover, not the same set of invoices
    -- see matched_avg_days_to_pay() for the fair, like-for-like comparison.
    """
    days = paid_days_map(invoices)
    return round(sum(days.values()) / len(days), 1) if days else None


def matched_avg_days_to_pay(baseline: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
    """The fair comparison: avg days to pay over invoices BOTH runs recovered.

    The plain avg_days_to_pay() figures are not directly comparable -- a run
    that gives up on the hard cases (and simply never recovers them) has a
    faster-looking average than one that goes after them and eventually wins
    most of them, purely because the hard ones drop out of the average
    entirely rather than counting as slow. Restricting to the intersection of
    what both runs actually recovered removes that selection effect.
    """
    common = set(baseline["paid_invoices"]) & set(agent["paid_invoices"])
    if not common:
        return {"n": 0, "baseline": None, "agent": None}
    return {
        "n": len(common),
        "baseline": round(sum(baseline["paid_invoices"][i] for i in common) / len(common), 1),
        "agent": round(sum(agent["paid_invoices"][i] for i in common) / len(common), 1),
    }


def _effectiveness_table(rows: dict[int, dict[str, int]]) -> dict[int, dict[str, Any]]:
    for row in rows.values():
        contacted = row["invoices_contacted"]
        row["effectiveness_pct"] = round(100 * row["recovered_here"] / contacted, 1) if contacted else 0.0
    return rows


def per_rung_effectiveness(
    history: dict[str, list[dict[str, Any]]], invoices_by_id: dict[str, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """For each rung: how many invoices were ever contacted there, and how many

    of those paid without needing to escalate any further (their last contact
    was at that rung and the invoice is now settled).
    """
    table: dict[int, dict[str, int]] = {r: {"invoices_contacted": 0, "recovered_here": 0}
                                        for r in (1, 2, 3)}
    for inv_id, contacts in history.items():
        if not contacts:
            continue
        for rung_id in {c["rung"] for c in contacts}:
            if rung_id in table:
                table[rung_id]["invoices_contacted"] += 1
        invoice = invoices_by_id.get(inv_id)
        last_rung = contacts[-1]["rung"]
        if invoice and invoice.get("status") == "paid" and last_rung in table:
            table[last_rung]["recovered_here"] += 1
    return _effectiveness_table(table)


def per_attempt_effectiveness(
    sent_count: dict[str, int], invoices_by_id: dict[str, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """The baseline's analogue of per_rung_effectiveness -- it has no rungs,

    only a fixed reminder number, so invoices are bucketed by how many
    reminders they had received when the run ended.
    """
    table: dict[int, dict[str, int]] = {n: {"invoices_contacted": 0, "recovered_here": 0}
                                        for n in range(1, BASELINE_MAX_MESSAGES + 1)}
    for inv_id, count in sent_count.items():
        for n in range(1, count + 1):
            if n in table:
                table[n]["invoices_contacted"] += 1
        invoice = invoices_by_id.get(inv_id)
        if invoice and invoice.get("status") == "paid" and count in table:
            table[count]["recovered_here"] += 1
    return _effectiveness_table(table)


def _exceptions(
    invoices: list[dict[str, Any]],
    buyers_by_id: dict[str, dict[str, Any]],
    persona_of: dict[str, str],
    reason_of: dict[str, str],
    last_rung_of: dict[str, int | None],
    as_of: date,
) -> list[dict[str, Any]]:
    """Every current invoice not fully paid, with the buyer, persona and why.

    persona is a simulator-only field -- fine here, this report is for us,
    not something the agent itself ever gets to read (tests/test_sim_isolation.py
    is what guards that boundary).
    """
    rows = []
    for invoice in _current(invoices):
        if invoice.get("status") == "paid":
            continue
        inv_id = invoice["invoice_id"]
        buyer = buyers_by_id.get(invoice["buyer_id"], {})
        rows.append({
            "invoice_id": inv_id,
            "buyer_id": invoice["buyer_id"],
            "buyer_name": buyer.get("name"),
            "persona": persona_of.get(invoice["buyer_id"]),
            "status": invoice.get("status"),
            "outstanding_paise": law.outstanding_paise(invoice, as_of),
            "days_overdue": watchdog.days_overdue(invoice, as_of),
            "disputed": bool(invoice.get("disputed")),
            "last_rung": last_rung_of.get(inv_id),
            "reason": reason_of.get(inv_id, "never contacted within the simulated window"),
        })
    rows.sort(key=lambda r: -r["outstanding_paise"])
    return rows


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
    buyers, invoices, persona_of, day0 = _load_world(seed)
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    history: dict[str, list[dict[str, Any]]] = {}
    promises_by_invoice: dict[str, list[dict[str, Any]]] = {}
    seen_rungs: dict[str, set[int]] = {}
    announced: set[str] = set()
    handoffs: set[str] = set()
    stops: set[str] = set()
    disputes: set[str] = set()
    handoff_reasons: dict[str, int] = {}
    stop_reasons: dict[str, int] = {}
    # The most recent brain.decide() outcome for every invoice, updated on
    # every visit (not just SEND) -- so an invoice still sitting inside an
    # active promise's grace period reports THAT as its reason, not silence.
    last_action_by_invoice: dict[str, dict[str, Any]] = {}
    messages_sent = 0
    narrative: list[str] = []
    last_day = day0

    audit.clear()
    audit.enable()

    with _forced_mock_mode():
        for offset in range(days):
            today = day0 + timedelta(days=offset)
            last_day = today

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
                last_action_by_invoice[inv_id] = {
                    "kind": action.kind, "rung": action.rung, "reason": action.reason,
                }

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
                        last_action_by_invoice[inv_id]["reason"] = (
                            f"the buyer disputed the invoice; {action.reason}")

                elif action.kind in (brain.HANDOFF, brain.STOP) and inv_id not in announced:
                    announced.add(inv_id)
                    bucket = _classify_reason(action.reason)
                    if action.kind == brain.HANDOFF:
                        handoffs.add(inv_id)
                        handoff_reasons[bucket] = handoff_reasons.get(bucket, 0) + 1
                    else:
                        stops.add(inv_id)
                        stop_reasons[bucket] = stop_reasons.get(bucket, 0) + 1
                    if verbose:
                        narrative.append(f"Day {offset + 1}: {buyer['name']} ({persona}) "
                                         f"{action.kind} -- {action.reason}")

    verify_conservation(invoices, last_day)
    invoices_by_id = {inv["invoice_id"]: inv for inv in invoices}
    reason_of = {inv_id: entry["reason"] for inv_id, entry in last_action_by_invoice.items()}
    last_rung_of = {inv_id: entry["rung"] for inv_id, entry in last_action_by_invoice.items()}
    return {
        "mode": "agent", "seed": seed, "days": days,
        "final": _totals(invoices, last_day),
        "messages_sent": messages_sent,
        "handoffs": len(handoffs), "stops": len(stops), "disputes": len(disputes),
        "handoff_reasons": handoff_reasons, "stop_reasons": stop_reasons,
        "avg_days_to_pay": avg_days_to_pay(invoices),
        "paid_invoices": paid_days_map(invoices),
        "per_rung": per_rung_effectiveness(history, invoices_by_id),
        "exceptions": _exceptions(invoices, buyers_by_id, persona_of, reason_of, last_rung_of, last_day),
        "narrative": narrative,
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


def _baseline_reason(invoice: dict[str, Any], sent: int) -> str:
    """Why the baseline hasn't recovered this invoice -- no brain to ask, so

    this is built from the fixed schedule's own state, not a decision.
    """
    if invoice.get("disputed"):
        return (f"the buyer disputed the invoice, but the baseline has no dispute "
                f"detection and kept its fixed schedule ({sent}/{BASELINE_MAX_MESSAGES} sent)")
    if sent >= BASELINE_MAX_MESSAGES:
        return f"all {BASELINE_MAX_MESSAGES} fixed reminders sent, no payment received"
    if sent == 0:
        return "not yet due, or the window ended before its first reminder"
    return f"only {sent}/{BASELINE_MAX_MESSAGES} fixed reminders sent before the window ended"


def run_baseline(seed: int, days: int, verbose: bool = False) -> dict[str, Any]:
    """Three fixed reminders, ten days apart, the same message for everyone.

    No score, no law, no rung, no dispute detection -- a dumb bot does not
    know it has been disputed, so it keeps sending until it hits its cap.
    Promises are still tracked and still mature (a buyer's commitment is real
    whether or not this bot is smart enough to reference it), which is what
    makes the comparison against the agent honest rather than stacked.
    """
    buyers, invoices, persona_of, day0 = _load_world(seed)
    buyers_by_id = {b["buyer_id"]: b for b in buyers}

    promises_by_invoice: dict[str, list[dict[str, Any]]] = {}
    sent_count: dict[str, int] = {}
    last_sent: dict[str, date] = {}
    disputes: set[str] = set()
    messages_sent = 0
    narrative: list[str] = []
    last_day = day0

    with _forced_mock_mode():
        for offset in range(days):
            today = day0 + timedelta(days=offset)
            last_day = today

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

    verify_conservation(invoices, last_day)
    invoices_by_id = {inv["invoice_id"]: inv for inv in invoices}
    reason_of = {
        inv_id: _baseline_reason(invoices_by_id[inv_id], count)
        for inv_id, count in sent_count.items()
    }
    return {
        "mode": "baseline", "seed": seed, "days": days,
        "final": _totals(invoices, last_day),
        "messages_sent": messages_sent,
        "handoffs": 0, "stops": 0, "disputes": len(disputes),
        "handoff_reasons": {}, "stop_reasons": {},
        "avg_days_to_pay": avg_days_to_pay(invoices),
        "paid_invoices": paid_days_map(invoices),
        "per_attempt": per_attempt_effectiveness(sent_count, invoices_by_id),
        "exceptions": _exceptions(invoices, buyers_by_id, persona_of, reason_of,
                                  {inv_id: BASELINE_RUNG for inv_id in sent_count}, last_day),
        "narrative": narrative,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

DEFAULT_RESULTS_PATH = Path(__file__).resolve().parents[1] / "report" / "out" / "results.json"


def _print_summary(label: str, report: dict[str, Any]) -> None:
    final = report["final"]
    days_to_pay = report["avg_days_to_pay"]
    print(f"{label}")
    print(f"  recovered            {format_inr(final['recovered_paise'], 'Rs '):>16}")
    print(f"  outstanding          {format_inr(final['outstanding_paise'], 'Rs '):>16}")
    print(f"  of which disputed    {format_inr(final['disputed_paise'], 'Rs '):>16} "
          f"({final['disputed_count']} invoices)")
    print(f"  avg days to pay      {(f'{days_to_pay:.1f}' if days_to_pay is not None else 'n/a'):>16}")
    print(f"  messages sent        {report['messages_sent']:>16}")
    print(f"  escalated to human   {report['handoffs']:>16}")
    print(f"  hard-stopped         {report['stops']:>16}")
    print(f"  not recovered        {len(report['exceptions']):>16}")


def _write_results(path: Path, seed: int, days: int, baseline: dict[str, Any],
                   agent: dict[str, Any], matched_days: dict[str, Any],
                   multi_seed: dict[str, Any] | None) -> None:
    payload = {
        "seed": seed, "days": days,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "baseline": baseline, "agent": agent,
        # A comparison-level figure, not something either agent computes about
        # itself: see matched_avg_days_to_pay()'s docstring for why the plain
        # avg_days_to_pay on each agent's OWN recovered set is not directly
        # comparable, and this fair, like-for-like figure is reported
        # alongside it rather than in its place.
        "matched_avg_days_to_pay": matched_days,
        "multi_seed": multi_seed,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


#: Extra seeds run for the multi-seed credibility table, on top of --seed.
#: Fixed and arbitrary -- picked once, never tuned to make the table look
#: better, which is the whole point of publishing more than one seed.
DEFAULT_EXTRA_SEEDS: tuple[int, ...] = (7, 13, 99, 2024, 555)


def multi_seed_summary(
    primary_seed: int, primary_baseline: dict[str, Any], primary_agent: dict[str, Any],
    extra_seeds: tuple[int, ...], days: int,
) -> dict[str, Any]:
    """Re-run the comparison on more seeds and report who won on each.

    The point is answering "did you just cherry-pick the seed?" directly
    rather than asking a judge to trust one number. Reuses the already-run
    primary seed's results instead of re-running it, and always leaves the
    on-disk dataset back on `primary_seed` before returning -- each extra
    seed regenerates data/seed/ for itself along the way (via _load_world).
    """
    def row(seed: int, baseline: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        matched = matched_avg_days_to_pay(baseline, agent)
        return {
            "seed": seed,
            "baseline_recovered_paise": baseline["final"]["recovered_paise"],
            "agent_recovered_paise": agent["final"]["recovered_paise"],
            "money_win": agent["final"]["recovered_paise"] >= baseline["final"]["recovered_paise"],
            "matched_n": matched["n"],
            "matched_baseline_days": matched["baseline"],
            "matched_agent_days": matched["agent"],
            "days_win": matched["n"] > 0 and matched["agent"] <= matched["baseline"],
        }

    rows = [row(primary_seed, primary_baseline, primary_agent)]
    for seed in extra_seeds:
        baseline = run_baseline(seed, days, verbose=False)
        agent = run_agent(seed, days, verbose=False)
        rows.append(row(seed, baseline, agent))

    generate.ensure_dataset(primary_seed)   # leave the dataset as we found it

    money_wins = sum(1 for r in rows if r["money_win"])
    days_eligible = [r for r in rows if r["matched_n"] > 0]
    days_wins = sum(1 for r in days_eligible if r["days_win"])
    return {
        "rows": rows,
        "money_win_rate": f"{money_wins}/{len(rows)}",
        "days_win_rate": f"{days_wins}/{len(days_eligible)}" if days_eligible else "n/a",
        "days_excluded": len(rows) - len(days_eligible),
    }


def main() -> int:
    enable_unicode_output()
    parser = argparse.ArgumentParser(description="Run the recovery simulation.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="random seed (default: 42)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="simulated days to run")
    parser.add_argument("--compare", action="store_true", help="run baseline and agent side by side")
    parser.add_argument("--verbose", action="store_true", help="print a daily narrative")
    parser.add_argument("--results-out", type=Path, default=DEFAULT_RESULTS_PATH,
                        help="where --compare writes results.json (default: report/out/results.json)")
    parser.add_argument("--extra-seeds", default=",".join(str(s) for s in DEFAULT_EXTRA_SEEDS),
                        help="comma-separated extra seeds for the multi-seed table under --compare "
                             "(default: the 5 fixed seeds above; pass \"\" to skip)")
    args = parser.parse_args()

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
        matched = matched_avg_days_to_pay(baseline, agent)
        if matched["n"]:
            print(f"avg days to pay, {matched['n']} invoices BOTH recovered "
                  f"(the fair comparison): baseline {matched['baseline']}, agent {matched['agent']}")

        extra_seeds = tuple(int(s) for s in args.extra_seeds.split(",") if s.strip())
        multi_seed = None
        if extra_seeds:
            print()
            print(f"running {len(extra_seeds)} more seeds for the multi-seed table: {extra_seeds}")
            multi_seed = multi_seed_summary(args.seed, baseline, agent, extra_seeds, args.days)
            print(f"agent won on rupees recovered in {multi_seed['money_win_rate']} seeds, "
                  f"on avg days-to-pay (fair comparison) in {multi_seed['days_win_rate']} seeds")

        _write_results(args.results_out, args.seed, args.days, baseline, agent, matched, multi_seed)
        print(f"results written to {args.results_out}")
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
