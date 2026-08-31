"""Buyer-level panel -- rolls invoice-level facts up to one buyer.

The rest of the system thinks in invoices. This module answers a different
question: "what does this buyer's whole relationship with us look like right
now?" -- total money at risk across every invoice they hold, not just the
oldest one; whether they generally keep their word; whether they reply at
all; and where each of their outstanding invoices currently sits in the
recovery process.

Every figure here is read straight off data the system already tracks --
score.py's own score/confidence/trend (reused unmodified, never
recalculated), watchdog's overdue math, promises.py's kept/broken/open
status, and the per-invoice send/outcome history sim/run_sim.py already
keeps for the day it is run. Nothing here is invented, predicted or
estimated -- see CLAUDE.md's W2 hard constraint. Where the data cannot
honestly support a figure (no promise history, no messages sent yet, a
broken promise that was never followed by a payment), the field is `None`
and the caller is expected to say so explicitly rather than print a
misleading number -- report/build_report.py is where that wording lives.

Pure aggregation, no rules that change what the agent does, no LLM calls.
Called once, at the end of a run, from sim/run_sim.py -- nothing here feeds
back into engine/brain.py this phase; see CLAUDE.md's W2 note on that.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from engine import watchdog
from engine.law import outstanding_paise as law_outstanding_paise

#: The four states an outstanding invoice can be in at report time, from the
#: last brain.decide() action recorded against it (engine.brain's own
#: WAIT/SEND/HANDOFF/STOP kinds). An invoice never yet seen by the brain --
#: not yet overdue -- has no recorded action at all.
NOT_YET_DUE, IN_LADDER, HANDED_OFF, STOPPED = "not_yet_due", "in_ladder", "handed_off", "stopped"

#: Phase 3 added engine.brain's payment_plan/counter_settle kinds -- both are
#: buyer-facing sends at an already-chosen rung, exactly like "send", so they
#: belong in the same "still being chased" bucket, not silently dropped into
#: neither in_ladder nor handed_off nor stopped.
_LADDER_KINDS = frozenset({"wait", "send", "payment_plan", "counter_settle"})


def _promise_reliability(
    promises: list[dict[str, Any]], invoices_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Made / kept / broken / in-flight, plus how late a broken promise ran.

    In-flight (status "open", not yet due) is neither kept nor broken -- it
    has not resolved yet and is excluded from the reliability percentage,
    the same convention engine.watchdog._settled_promise_counts already
    uses for early warnings.

    "Average days late" is deliberately NOT broken_on - promised_date: the
    simulator's daily sweep marks a promise broken the very next day it
    could, so that gap is a constant ~1 day for every promise regardless of
    buyer behaviour -- a sweep-cadence artifact, not a signal. Instead it is
    measured against the invoice's own paid_date: for a broken promise whose
    invoice went on to be paid, days_late = paid_date - promised_date. A
    broken promise on an invoice still unpaid has nothing to average yet --
    there is no "how late" for money that has not arrived -- so it counts
    toward `broken` but not toward `avg_days_late`.
    """
    kept = [p for p in promises if p.get("status") == "kept"]
    broken = [p for p in promises if p.get("status") == "broken"]
    in_flight = [p for p in promises if p.get("status") == "open"]
    settled = len(kept) + len(broken)

    resolved_late_days: list[int] = []
    for promise in broken:
        invoice = invoices_by_id.get(promise.get("invoice_id"))
        if invoice and invoice.get("status") == "paid" and invoice.get("paid_date"):
            late = (date.fromisoformat(invoice["paid_date"])
                    - date.fromisoformat(promise["promised_date"])).days
            resolved_late_days.append(late)

    return {
        "made": len(promises),
        "kept": len(kept),
        "broken": len(broken),
        "in_flight": len(in_flight),
        "reliability_pct": round(100 * len(kept) / settled, 1) if settled else None,
        "avg_days_late": (round(sum(resolved_late_days) / len(resolved_late_days), 1)
                          if resolved_late_days else None),
    }


def _response_rate(history_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Replies received against messages sent, over one buyer's invoices.

    A "reply" is any contact outcome other than "no_reply" -- a payment, a
    promise, a dispute or an unexplained partial payment all count as the
    buyer having engaged; only silence does not. Drawn straight from the
    per-invoice history sim/run_sim.py already keeps (one entry per message
    actually sent, tagged with what happened next), so this needs nothing
    beyond what the day loop already produces.
    """
    sent = len(history_entries)
    replied = sum(1 for entry in history_entries if entry.get("outcome") != "no_reply")
    return {
        "messages_sent": sent,
        "replies": replied,
        "response_rate_pct": round(100 * replied / sent, 1) if sent else None,
    }


def _recovery_state(
    invoice_ids: list[str], last_action_by_invoice: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """How many of a buyer's outstanding invoices sit in each recovery state.

    Only called with OUTSTANDING invoice ids -- a paid invoice's own STOP
    action ("the invoice is settled") never reaches this function, so `stop`
    here can only mean a genuine hard stop (opted out), never a completed
    recovery being miscounted as one.
    """
    counts = {NOT_YET_DUE: 0, IN_LADDER: 0, HANDED_OFF: 0, STOPPED: 0}
    for invoice_id in invoice_ids:
        action = last_action_by_invoice.get(invoice_id)
        if action is None:
            counts[NOT_YET_DUE] += 1
        elif action.get("kind") in _LADDER_KINDS:
            counts[IN_LADDER] += 1
        elif action.get("kind") == "handoff":
            counts[HANDED_OFF] += 1
        elif action.get("kind") == "stop":
            counts[STOPPED] += 1
    return counts


def buyer_panel(
    buyers: list[dict[str, Any]],
    invoices_by_buyer: dict[str, list[dict[str, Any]]],
    promises_by_invoice: dict[str, list[dict[str, Any]]],
    history: dict[str, list[dict[str, Any]]],
    scores_by_buyer: dict[str, dict[str, Any]],
    last_action_by_invoice: dict[str, dict[str, Any]],
    today: date,
    *,
    invalid_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """One row per buyer who currently has at least one outstanding invoice.

    A buyer with nothing outstanding (every invoice paid, or none of theirs
    valid) does not appear -- this panel is for "who needs attention", not a
    directory of every buyer that has ever existed. Sorted worst-first by
    total outstanding, matching engine.score.score_all's and
    sim.run_sim._exceptions's own worst-first convention.

    Args:
        buyers: every buyer record.
        invoices_by_buyer: buyer_id -> that buyer's invoices (data.store.
            invoices_by_buyer), current and any history the caller included.
        promises_by_invoice: invoice_id -> promises recorded on it.
        history: invoice_id -> contacts made on it (sim.run_sim's own
            per-day record: date, rung, channel, outcome).
        scores_by_buyer: buyer_id -> engine.score.score_buyer output, reused
            exactly as computed there -- this module never recalculates it.
        last_action_by_invoice: invoice_id -> the most recent engine.brain.
            decide() Action (kind/rung/reason) recorded against it.
        today: the simulation clock overdue-ness is measured against.
        invalid_ids: invoice ids engine.validate flagged as malformed --
            excluded from every total here, the same way sim.run_sim._totals
            excludes them from the headline recovered/outstanding figures.

    Returns:
        A list of per-buyer dicts. Every number traces back to invoices,
        promises, messages and payments already on file; see the module
        docstring for what a `None` field means and why.
    """
    rows: list[dict[str, Any]] = []
    for buyer in buyers:
        buyer_id = buyer["buyer_id"]
        all_invoices = invoices_by_buyer.get(buyer_id, [])
        outstanding_invoices = [
            inv for inv in all_invoices
            if inv["invoice_id"] not in invalid_ids and watchdog.is_unsettled(inv)
        ]
        if not outstanding_invoices:
            continue

        overdue = [inv for inv in outstanding_invoices if watchdog.is_overdue(inv, today)]
        invoices_by_id = {inv["invoice_id"]: inv for inv in all_invoices}
        buyer_promises = [
            p for inv in all_invoices for p in promises_by_invoice.get(inv["invoice_id"], [])
        ]
        buyer_history_entries = [
            entry for inv in all_invoices for entry in history.get(inv["invoice_id"], [])
        ]
        outstanding_ids = [inv["invoice_id"] for inv in outstanding_invoices]

        rows.append({
            "buyer_id": buyer_id,
            "name": buyer.get("name"),
            "outstanding_paise": sum(law_outstanding_paise(inv, today) for inv in outstanding_invoices),
            "overdue_count": len(overdue),
            "oldest_days_overdue": (
                max(watchdog.days_overdue(inv, today) for inv in overdue) if overdue else None
            ),
            "score": scores_by_buyer.get(buyer_id),
            "promises": _promise_reliability(buyer_promises, invoices_by_id),
            "response": _response_rate(buyer_history_entries),
            "recovery_state": _recovery_state(outstanding_ids, last_action_by_invoice),
        })

    rows.sort(key=lambda r: (-r["outstanding_paise"], r["buyer_id"]))
    return rows
