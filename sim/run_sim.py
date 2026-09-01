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
from engine import ability_willingness
from engine import audit, brain, channels, consolidate, law, llm, outcomes, promises, validate, watchdog, writer
from engine import buyer_panel as buyer_panel_engine
from engine import score as score_engine
from engine.config import rules
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


def _apply_payment(invoice: dict[str, Any], amount_paise: int, today: date,
                  ledger: outcomes.OutcomeLedger | None = None) -> None:
    """Ground truth: money actually landed. Never more than what is owed.

    Args:
        ledger: the run's engine.outcomes ledger, if it is collecting. Told
            the CLAMPED amount, below, and only when money really moved --
            this is the single choke point every payment in the simulation
            goes through, which is exactly why the hook lives here rather
            than at the four call sites that would each have to remember it.
    """
    remaining = law.outstanding_paise(invoice, today)
    amount_paise = max(0, min(int(amount_paise), remaining))
    if amount_paise <= 0:
        return
    if ledger is not None:
        ledger.record_payment(invoice_id=invoice["invoice_id"], day=today,
                              amount_paise=amount_paise)
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
    ledger: outcomes.OutcomeLedger | None = None,
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
                _apply_payment(invoice, int(remaining * rng.uniform(0.4, 0.7)), today, ledger)
            else:
                _apply_payment(invoice, remaining, today, ledger)
            promises.mark_kept(promise, today, log=log)
        promises.sweep(plist, today, log=log)


def _apply_reaction(
    invoice: dict[str, Any],
    plist: list[dict[str, Any]],
    reaction: dict[str, Any],
    today: date,
    seed: int,
    log: bool,
    ledger: outcomes.OutcomeLedger | None = None,
) -> str:
    """Apply what the persona did and return the history outcome tag."""
    outcome = reaction["outcome"]
    if outcome == personas.PAY_FULL:
        _apply_payment(invoice, law.outstanding_paise(invoice, today), today, ledger)
        return "paid_full"
    if outcome == personas.PAY_PARTIAL:
        rng = _rng(seed, invoice["invoice_id"], today, "partial_amount")
        remaining = law.outstanding_paise(invoice, today)
        _apply_payment(invoice, int(remaining * rng.uniform(0.35, 0.6)), today, ledger)
        # A part-payment with no explanation is exactly the ambiguous case
        # engine.brain._is_ambiguous looks for -- this is what exercises it.
        return "unclear_reply"
    if outcome in (personas.PROMISE, personas.DISPUTE):
        parsed = promises.parse_reply(
            reaction["reply"], today, variant=reaction["variant"],
            invoice_id=invoice["invoice_id"], buyer_id=invoice["buyer_id"],
            outstanding_paise=law.outstanding_paise(invoice, today), log=log,
        )
        promises.apply_reply(parsed, invoice, plist, today, log=log)
        return "promise_made" if outcome == personas.PROMISE else "disputed"
    return "no_reply"


def _totals(invoices: list[dict[str, Any]], today: date,
           invalid_ids: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Headline money totals over CURRENT, VALID invoices only.

    A malformed invoice (engine.validate) is excluded here too, not just from
    the watchdog queue: a single bad amount_paise must never move the
    headline recovered/outstanding figures, or non-negotiable #5 (results
    measured honestly) is broken by a data bug rather than a real result. It
    still shows up individually in the exceptions list -- see _exceptions().
    """
    current = [inv for inv in _current(invoices) if inv["invoice_id"] not in invalid_ids]
    disputed = [inv for inv in current if inv.get("disputed")]
    return {
        "day": today.isoformat(),
        "recovered_paise": sum(int(inv.get("amount_paid_paise", 0)) for inv in current),
        "outstanding_paise": sum(law.outstanding_paise(inv, today) for inv in current),
        "disputed_paise": sum(law.outstanding_paise(inv, today) for inv in disputed),
        "disputed_count": len(disputed),
    }


def verify_conservation(invoices: list[dict[str, Any]], as_of: date,
                        invalid_ids: frozenset[str] = frozenset()) -> None:
    """Money in must equal money accounted for. No invoice may leak or gain paise.

    Excludes invalid invoices (engine.validate): the conservation invariant
    assumes amount_paise is a sane starting point, which is exactly what a
    malformed invoice violates (e.g. TC-054's negative amount) -- and one is
    never touched by _apply_payment while excluded from the watchdog queue,
    so there is nothing of this run's doing left to verify on it anyway.
    """
    for invoice in _current(invoices):
        if invoice["invoice_id"] in invalid_ids:
            continue
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


def edge_case_counts(
    invoices: list[dict[str, Any]], day0: date,
    promises_by_invoice: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """How many invoices in THIS run's world actually exercise the E1
    (superseded promise, TC-014) / E2 (malformed invoice) fixes.

    For the multi-seed table's credibility note (W4 advisor item 2): a
    "6/6" win reads very differently as "despite these edge cases" than as
    "these edge cases never came up". Malformed is counted at day0, not
    whatever day the caller's own run ended on -- a clock-relative defect
    (TC-050's future issue_date) can validly stop being invalid by the end
    of a long run (see run_agent()'s own comment on checking validity at
    last_day), but it still needed E2's validation to handle safely while
    it WAS invalid, and this count is about whether that ever happened, not
    the run's final verdict on it. The structural fields validate.py checks
    (amount, dates, duplicates) never change after generation, so checking
    against day0 with whatever invoices list the caller has now (mutated by
    payments or not) gives the same answer checking at the very start would
    have.

    A "superseded promise" is simply an invoice with more than one promise
    ever recorded against it -- exactly the TC-014 scenario: a buyer
    renegotiating before their first promise fell due.
    """
    day0_invalid = validate.audit_invalid(invoices, day0, log=False)
    return {
        "malformed_invoices": len(day0_invalid),
        "superseded_promise_invoices": sum(
            1 for plist in promises_by_invoice.values() if len(plist) > 1),
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
    invalid_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Every current invoice not fully paid, with the buyer, persona and why.

    This is also where a malformed invoice (engine.validate) surfaces -- it is
    excluded from the watchdog queue so it never reaches engine/law.py or
    engine/brain.py, but it is still a current, unpaid invoice, so it lands in
    this same list like any other unrecovered one. reason_of is expected to
    already carry the validation reason for it (see run_agent/run_baseline),
    so no separate "why wasn't this recovered" mechanism exists for it.
    days_overdue cannot be computed for one -- its own dates are what is
    wrong -- so it is left as None rather than calling watchdog.days_overdue()
    and risking the exact crash validation exists to prevent.

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
        invalid = inv_id in invalid_ids
        rows.append({
            "invoice_id": inv_id,
            "buyer_id": invoice["buyer_id"],
            "buyer_name": buyer.get("name"),
            "persona": persona_of.get(invoice["buyer_id"]),
            "status": invoice.get("status"),
            "outstanding_paise": law.outstanding_paise(invoice, as_of),
            "days_overdue": None if invalid else watchdog.days_overdue(invoice, as_of),
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

def _notable_early_warnings(
    invoices: list[dict[str, Any]],
    promises_list: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    """watchdog.early_warnings() reports a real risk_band (low/watch/high)
    for every invoice in the window -- "low" is a genuine, computed value,
    not an absence. This is the ONE place that decides what is worth writing
    to the audit trail: low band is filtered out HERE, not inside
    early_warnings() itself, so a low-band invoice structurally can never
    produce an early_warning_raised entry (and therefore never appears in
    the report's early-warning section either, since that section only ever
    reads back what was logged -- see report/build_report.py
    _early_warning_rows()). Pulled out as its own function, rather than left
    inline in run_agent()'s day loop, specifically so this decision can be
    unit-tested directly instead of only inferred from one seed's own data
    happening not to exercise it.
    """
    return [w for w in watchdog.early_warnings(invoices, promises_list, scores, today)
            if w["risk_band"] != "low"]


def _raise_early_warnings(
    invoices: list[dict[str, Any]],
    promises_list: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    today: date,
    warned: set[str],
) -> None:
    """Writes one early_warning_raised audit entry per invoice, the first day
    it crosses into watch/high -- never "low", which _notable_early_warnings()
    already excluded. `warned` is mutated in place so a later call (the next
    simulated day) does not repeat an entry already logged.
    """
    for warning in _notable_early_warnings(invoices, promises_list, scores, today):
        if warning["invoice_id"] in warned:
            continue
        warned.add(warning["invoice_id"])
        audit.record(
            invoice_id=warning["invoice_id"], action="early_warning_raised",
            reason=(f"{warning['risk_band']} risk, {warning['signals_triggered']} "
                    f"signal(s): {'; '.join(warning['reasons'])}"),
            source="rule", today=today, buyer_id=warning["buyer_id"],
            actor="watchdog", detail=warning,
        )


def run_agent(
    seed: int, days: int, verbose: bool = False, ev_mode: bool = False,
) -> dict[str, Any]:
    """Run the full agent (watchdog -> score -> law -> brain -> writer ->
    channels -> persona reacts -> promises) over `days` simulated days.

    This is the one place in the whole system that produces a full,
    reproducible audit trail: the log is cleared at the start (a fresh run
    should leave a fresh, self-consistent trail for the seed and window it
    was asked for) and every decision, draft and delivery is written with
    log=True, exactly as production would.

    Args:
        ev_mode: Phase 4's third experiment arm. False (the default) passes
            config=None to every brain.decide() call, exactly as before this
            phase -- decide() resolves that to the cached rules() itself, so
            this is byte-identical to pre-Phase-4 output (see
            tests/test_run_sim.py's snapshot-diff test). True passes
            config/rules.yaml's own settings with brain.ev_mode forced "on",
            computed once here rather than per invoice -- the same override
            shape tests/test_brain.py's own ev_config() helper uses, not a
            new pattern. sim.personas.react() already differentiates
            payment_plan/counter_settle reactions (Phase 4 Part A); this is
            what actually lets a real run reach them.
    """
    decide_config = None
    if ev_mode:
        base_config = rules()
        decide_config = {**base_config, "brain": {**base_config.get("brain", {}), "ev_mode": "on"}}

    # Outcome attribution (engine/outcomes.py). Collects during the run,
    # judges once at the end, and is READ BY NOBODY inside the loop -- no
    # brain.decide() call, no rung choice and no stop rule can see a record
    # it holds, which is what keeps adding it a pure observation rather than
    # a behaviour change. The mode label distinguishes the ablation's two
    # agent arms in the shared outcomes.jsonl.
    ledger = outcomes.OutcomeLedger(mode="agent_ev" if ev_mode else "agent", seed=seed)

    buyers, invoices, persona_of, day0 = _load_world(seed)
    buyers_by_id = {b["buyer_id"]: b for b in buyers}
    # Built once: invoice dicts are mutated in place by _apply_payment (never
    # replaced), so this stays valid for the whole run without refreshing.
    invoices_by_id = {inv["invoice_id"]: inv for inv in invoices}

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
    # messages_sent counts outbound ENVELOPES (one per buyer/tier bundle, per
    # day it goes out) -- what CLAUDE.md's W3 plan expects to drop.
    # invoice_contacts counts, per invoice, every day it was part of a send
    # -- the OLD messages_sent semantics, preserved under a new name so the
    # "did chasing actually change" question stays answerable. Both are
    # incremented from the exact same bundle loop below, never independently.
    messages_sent = 0
    invoice_contacts = 0
    narrative: list[str] = []
    last_day = day0
    # Logged once per invoice, the first day it is seen -- an invoice sitting
    # inside the window every day of a long run should not spam one audit
    # entry per day for the same warning.
    warned: set[str] = set()

    audit.clear()
    audit.enable()

    with _forced_mock_mode():
        for offset in range(days):
            today = day0 + timedelta(days=offset)
            last_day = today

            _advance_promises(invoices, promises_by_invoice, persona_of, today, seed, log=True,
                              ledger=ledger)

            queue = watchdog.overdue_invoices(invoices, today)
            grouped = store.invoices_by_buyer(invoices)
            scores = {s["buyer_id"]: s for s in score_engine.score_all(buyers, grouped, today)}

            all_promises = [p for plist in promises_by_invoice.values() for p in plist]
            _raise_early_warnings(invoices, all_promises, scores, today, warned)

            # Phase 1: decide -- unchanged, per invoice, exactly as before.
            # engine.brain.decide() never knows a bundle exists; every stop
            # rule, promise grace and rung selection is computed exactly as
            # if consolidation did not exist (CLAUDE.md W3 plan, point b).
            #
            # Phase 3: the score handed to decide() is now the two-axis one
            # (engine.ability_willingness.two_axis_score()), computed fresh
            # per INVOICE rather than reused from the per-buyer `scores` dict
            # above -- ability_for_invoice sizes ability to what is actually
            # owed on THIS invoice, per its own docstring, and a buyer with
            # several invoices of different sizes needs a different ability
            # reading for each. It is a strict superset of what score_buyer()
            # returns (same score/confidence/history_count, plus
            # ability/willingness/quadrant), so this changes nothing about
            # decide()'s output while config/rules.yaml's brain.ev_mode
            # stays "off" -- see tests/test_brain.py's snapshot test. `scores`
            # itself is untouched and keeps feeding early warnings and the
            # buyer panel, both of which read the legacy score_buyer() shape.
            day_actions: list[brain.Action] = []
            # This day's quadrant per invoice, for the outcome ledger only --
            # two_axis_score() is already computed below for brain.decide(),
            # so this is a stash, not a second computation. An invoice absent
            # from it (nothing this module can currently produce, but a
            # future caller might) records quadrant null rather than a guess.
            quadrant_of: dict[str, str | None] = {}
            for invoice in queue:
                inv_id = invoice["invoice_id"]
                buyer = buyers_by_id[invoice["buyer_id"]]
                persona = persona_of[buyer["buyer_id"]]
                position = law.legal_position(invoice, today)
                plist = promises_by_invoice.setdefault(inv_id, [])
                hist = history.setdefault(inv_id, [])

                two_axis = ability_willingness.two_axis_score(
                    buyer, grouped.get(buyer["buyer_id"], []), today, invoice=invoice)
                action = brain.decide(invoice, buyer, two_axis, position,
                                      promises=plist, history=hist, log=True,
                                      config=decide_config)
                quadrant_of[inv_id] = two_axis.get("quadrant")
                last_action_by_invoice[inv_id] = {
                    "kind": action.kind, "rung": action.rung, "reason": action.reason,
                }
                day_actions.append(action)

                if action.kind in (brain.HANDOFF, brain.STOP) and inv_id not in announced:
                    announced.add(inv_id)
                    bucket = _classify_reason(action.reason)
                    if action.kind == brain.HANDOFF:
                        handoffs.add(inv_id)
                        handoff_reasons[bucket] = handoff_reasons.get(bucket, 0) + 1
                        # Recorded here, once, on the day the human is
                        # actually handed the case -- not on every later day
                        # decide() keeps returning HANDOFF for an invoice
                        # already announced. A STOP is deliberately NOT
                        # recorded: it is the decision to CEASE acting, and
                        # crediting a later payment to it would say the
                        # agent recovered money by giving up.
                        #
                        # READ THIS BEFORE TRUSTING A HANDOFF'S SCORE: it
                        # will be 0% recovered, always, in every run. Not a
                        # bug in the attribution -- the simulator has no
                        # model of what the MSME owner does after taking the
                        # case over, so no money can ever arrive behind a
                        # handoff here. Its outcome rows are the honest
                        # record of an action this world cannot score, not
                        # evidence that handoffs do not work, and anything
                        # that later learns from this file has to exclude
                        # them rather than read them as failures.
                        ledger.record_action(
                            invoice_id=inv_id, buyer_id=buyer["buyer_id"], day=today,
                            quadrant=quadrant_of.get(inv_id), action_kind=action.kind,
                            rung=action.rung,
                            outstanding_paise_at_action=law.outstanding_paise(invoice, today),
                        )
                    else:
                        stops.add(inv_id)
                        stop_reasons[bucket] = stop_reasons.get(bucket, 0) + 1
                    if verbose:
                        narrative.append(f"Day {offset + 1}: {buyer['name']} ({persona}) "
                                         f"{action.kind} -- {action.reason}")

            # Phase 2: consolidate -- group today's SEND decisions by buyer
            # and rung tier (engine/consolidate.py), then draft and send ONE
            # envelope per bundle instead of one per invoice. A buyer with a
            # single eligible invoice today still goes through this same
            # path as a "bundle of one" -- there is no separate single-
            # invoice code path left to drift from this one.
            for bundle in consolidate.bundle_sends(day_actions):
                buyer_id = bundle["buyer_id"]
                buyer = buyers_by_id[buyer_id]
                persona = persona_of[buyer_id]
                bundle_actions = bundle["actions"]

                drafted = writer.write_consolidated_message(
                    bundle_actions, invoices_by_id=invoices_by_id, buyer=buyer,
                    score=scores[buyer_id], promises_by_invoice=promises_by_invoice,
                    today=today, log=True,
                )
                target = buyer.get("preferred_channel", "email")
                to = buyer.get("contact_email") if target == "email" else buyer.get("contact_phone", "")
                invoice_rungs = {action.invoice_id: action.rung for action in bundle_actions}
                channels.send_consolidated(target, to, drafted, invoice_rungs=invoice_rungs,
                                           buyer_id=buyer_id, today=today, enabled=False, log=True)
                messages_sent += 1

                bundle_ids = [action.invoice_id for action in bundle_actions]
                for action in bundle_actions:
                    inv_id = action.invoice_id
                    invoice = invoices_by_id[inv_id]
                    plist = promises_by_invoice.setdefault(inv_id, [])
                    hist = history.setdefault(inv_id, [])
                    invoice_contacts += 1

                    # Recorded BEFORE the persona reacts, so
                    # outstanding_paise_at_action is what was owed when the
                    # message went out, and so a same-day payment triggered by
                    # this very message is credited to it (the ledger's own
                    # seq ordering, not a special case).
                    ledger.record_action(
                        invoice_id=inv_id, buyer_id=buyer_id, day=today,
                        quadrant=quadrant_of.get(inv_id), action_kind=action.kind,
                        rung=action.rung,
                        outstanding_paise_at_action=law.outstanding_paise(invoice, today),
                    )

                    rng = _rng(seed, inv_id, today, "react")
                    reaction = personas.react(persona, action.rung, rng, action_kind=action.kind)
                    outcome = _apply_reaction(invoice, plist, reaction, today, seed, log=True,
                                              ledger=ledger)
                    hist.append({"date": today.isoformat(), "rung": action.rung,
                                "channel": target, "outcome": outcome,
                                "bundle_invoice_ids": bundle_ids})

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

    # A malformed invoice (engine.validate) is found here, once, against the
    # clock as it stands at the END of the run -- not at day0. TC-050's own
    # defect (an issue_date in the future) is inherently clock-relative: the
    # watchdog already re-checked it fresh every single day inside the loop
    # above (so it was correctly kept out of the queue for every day it was
    # genuinely invalid), but by the time a long run ends, "the future" may
    # already have arrived, and by then it is just an ordinary invoice --
    # judged, like any other, on whatever brain.decide() actually did with
    # it. Checking here at last_day, once, keeps that judgment honest instead
    # of freezing a stale day0 verdict for the whole run.
    validation_reasons = validate.audit_invalid(invoices, last_day, log=True)
    invalid_ids = frozenset(validation_reasons)

    # Judged and written once, after the last day: an action's verdict is not
    # knowable until its whole attribution horizon has elapsed. Actions taken
    # in the final learning.attribution_horizon_days of the window are
    # therefore judged against a horizon the run ended inside -- they can only
    # ever look worse than they were, never better, so the bias is the
    # conservative direction and is stated here rather than corrected for.
    attribution = ledger.write()

    counted_edge_cases = edge_case_counts(invoices, day0, promises_by_invoice)

    verify_conservation(invoices, last_day, invalid_ids)
    # invoices_by_id was already built once, at the top of this function.
    reason_of = {
        **validation_reasons,
        **{inv_id: entry["reason"] for inv_id, entry in last_action_by_invoice.items()},
    }
    last_rung_of = {inv_id: entry["rung"] for inv_id, entry in last_action_by_invoice.items()}

    # Report-only rollup, computed once at the very end from the same final
    # state everything above already settled on -- see engine/buyer_panel.py's
    # own docstring, and CLAUDE.md's W2 note: nothing above this line (every
    # brain.decide() call in the day loop already happened) ever sees this,
    # so it has zero influence on what the agent did this run.
    grouped_final = store.invoices_by_buyer(invoices)
    scores_final = {s["buyer_id"]: s for s in score_engine.score_all(buyers, grouped_final, last_day)}
    panel = buyer_panel_engine.buyer_panel(
        buyers, grouped_final, promises_by_invoice, history, scores_final,
        last_action_by_invoice, last_day, invalid_ids=invalid_ids,
    )

    return {
        "mode": "agent", "seed": seed, "days": days, "ev_mode": ev_mode,
        "final": _totals(invoices, last_day, invalid_ids),
        "messages_sent": messages_sent,
        # Kept alongside messages_sent, not in place of it: messages_sent now
        # counts outbound envelopes (bundled), invoice_contacts is the OLD
        # per-invoice-contact semantics -- see the day-loop comment above and
        # CLAUDE.md's W3 plan, point g. Sum(invoice_contacts) is what
        # messages_sent would have been before consolidation.
        "invoice_contacts": invoice_contacts,
        "edge_case_counts": counted_edge_cases,
        "handoffs": len(handoffs), "stops": len(stops), "disputes": len(disputes),
        "handoff_reasons": handoff_reasons, "stop_reasons": stop_reasons,
        "avg_days_to_pay": avg_days_to_pay(invoices),
        "paid_invoices": paid_days_map(invoices),
        "per_rung": per_rung_effectiveness(history, invoices_by_id),
        "exceptions": _exceptions(invoices, buyers_by_id, persona_of, reason_of, last_rung_of,
                                  last_day, invalid_ids),
        "buyer_panel": panel,
        # Report-only, like buyer_panel above: the totals from
        # engine/outcomes.py, so the unattributed payments are COUNTED in the
        # run's own output and not merely left sitting in a file nobody opens.
        "outcomes": attribution["summary"],
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
    # The baseline is instrumented exactly like the agent, on purpose: the
    # ablation only means anything if both arms are measured the same way.
    # quadrant is null on every record here -- this bot has no two-axis score
    # and never will, and writing null is the honest version of that (see
    # engine/outcomes.py's record_action docstring).
    ledger = outcomes.OutcomeLedger(mode="baseline", seed=seed)

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

            _advance_promises(invoices, promises_by_invoice, persona_of, today, seed, log=False,
                              ledger=ledger)

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

                ledger.record_action(
                    invoice_id=inv_id, buyer_id=buyer["buyer_id"], day=today,
                    quadrant=None, action_kind="reminder", rung=BASELINE_RUNG,
                    outstanding_paise_at_action=law.outstanding_paise(invoice, today),
                )

                rng = _rng(seed, inv_id, today, "baseline_react")
                reaction = personas.react(persona, BASELINE_RUNG, rng)
                plist = promises_by_invoice.setdefault(inv_id, [])
                outcome = _apply_reaction(invoice, plist, reaction, today, seed, log=False,
                                          ledger=ledger)
                if outcome == "disputed":
                    disputes.add(inv_id)
                if verbose:
                    narrative.append(
                        f"Day {offset + 1}: [baseline] {buyer['name']} ({persona}) "
                        f"reminder {sent_count[inv_id]}/{BASELINE_MAX_MESSAGES} -> {outcome}")

    # Checked once here, against the clock at the END of the run -- see the
    # matching comment in run_agent() for why last_day and not day0.
    # Never logged (log=False): run_agent() is the one place a full audit
    # trail is produced, per this function's own docstring above.
    validation_reasons = validate.audit_invalid(invoices, last_day, log=False)
    invalid_ids = frozenset(validation_reasons)

    verify_conservation(invoices, last_day, invalid_ids)
    attribution = ledger.write()
    invoices_by_id = {inv["invoice_id"]: inv for inv in invoices}
    reason_of = {
        **validation_reasons,
        **{inv_id: _baseline_reason(invoices_by_id[inv_id], count)
           for inv_id, count in sent_count.items()},
    }
    return {
        "mode": "baseline", "seed": seed, "days": days,
        "final": _totals(invoices, last_day, invalid_ids),
        "messages_sent": messages_sent,
        "handoffs": 0, "stops": 0, "disputes": len(disputes),
        "handoff_reasons": {}, "stop_reasons": {},
        "avg_days_to_pay": avg_days_to_pay(invoices),
        "paid_invoices": paid_days_map(invoices),
        "per_attempt": per_attempt_effectiveness(sent_count, invoices_by_id),
        "outcomes": attribution["summary"],
        "exceptions": _exceptions(invoices, buyers_by_id, persona_of, reason_of,
                                  {inv_id: BASELINE_RUNG for inv_id in sent_count},
                                  last_day, invalid_ids),
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
                   multi_seed: dict[str, Any] | None,
                   agent_ev: dict[str, Any] | None = None) -> None:
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
    # Phase 4's third arm, added additively: an OLD results.json (or code
    # still reading one) has no "agent_ev" key at all, and report/
    # build_report.py's own .get("agent_ev") reads degrade to the existing
    # two-column layout when it is absent -- see that module's own comment.
    if agent_ev is not None:
        payload["agent_ev"] = agent_ev
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


#: Extra seeds run for the multi-seed credibility table, on top of --seed.
#: Fixed and arbitrary -- picked once, never tuned to make the table look
#: better, which is the whole point of publishing more than one seed.
DEFAULT_EXTRA_SEEDS: tuple[int, ...] = (7, 13, 99, 2024, 555)


def multi_seed_summary(
    primary_seed: int, primary_baseline: dict[str, Any], primary_agent: dict[str, Any],
    extra_seeds: tuple[int, ...], days: int,
    *, primary_agent_ev: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-run the comparison on more seeds and report who won on each.

    The point is answering "did you just cherry-pick the seed?" directly
    rather than asking a judge to trust one number. Reuses the already-run
    primary seed's results instead of re-running it, and always leaves both
    the on-disk dataset and the audit trail back on `primary_seed` before
    returning -- each extra seed regenerates data/seed/ for itself along the
    way (via _load_world) and clears the trail (via run_agent).

    Args:
        primary_agent_ev: Phase 4's third arm (run_agent(..., ev_mode=True)
            for the primary seed), reused the same way primary_baseline/
            primary_agent already are. None (the default) skips the ablation
            entirely -- every row is exactly the baseline_*/agent_* shape
            this function always produced, unchanged. Passing it in adds
            agent_ev_* keys to every row, additively, and re-runs the SAME
            ev_mode=True arm for every extra seed too, so the ablation is
            judged on the identical seed set as the existing 6/6 comparison,
            not a separately chosen one.
    """
    def row(seed: int, baseline: dict[str, Any], agent: dict[str, Any],
            agent_ev: dict[str, Any] | None) -> dict[str, Any]:
        matched = matched_avg_days_to_pay(baseline, agent)
        edge = agent["edge_case_counts"]
        result = {
            "seed": seed,
            "baseline_recovered_paise": baseline["final"]["recovered_paise"],
            "agent_recovered_paise": agent["final"]["recovered_paise"],
            "money_win": agent["final"]["recovered_paise"] >= baseline["final"]["recovered_paise"],
            "matched_n": matched["n"],
            "matched_baseline_days": matched["baseline"],
            "matched_agent_days": matched["agent"],
            "days_win": matched["n"] > 0 and matched["agent"] <= matched["baseline"],
            # W4 advisor item 2: how many invoices in THIS seed's world
            # actually exercise the E1 (superseded promise) / E2 (malformed
            # invoice) fixes -- so a "6/6" win reads as "despite these edge
            # cases", not "in the absence of them". See CLAUDE.md's W4 note
            # for the seed-42/seed-555 investigation behind this column.
            "malformed_invoices": edge["malformed_invoices"],
            "superseded_promise_invoices": edge["superseded_promise_invoices"],
        }
        if agent_ev is not None:
            # The ablation's own comparison basis is agent_ev vs. agent (ev
            # OFF) -- not vs. baseline -- per this phase's brief: "does the
            # negotiation layer actually add recovery on top of the existing
            # agent". Whether the EV arm still beats the naive baseline is
            # already implied (agent already does, per money_win above) and
            # is not this ablation's own question.
            result["agent_ev_recovered_paise"] = agent_ev["final"]["recovered_paise"]
            result["agent_ev_money_win"] = (
                agent_ev["final"]["recovered_paise"] >= agent["final"]["recovered_paise"]
            )
        return result

    rows = [row(primary_seed, primary_baseline, primary_agent, primary_agent_ev)]

    # The primary seed's trail is on disk right now, because its run_agent()
    # has already finished. Every extra seed's run_agent() starts by clearing
    # it, so hold the bytes here: without this, results.json reports the
    # primary seed while audit/audit_log.jsonl holds the LAST extra seed's
    # run, and nothing on disk says so. Restoring costs nothing -- it is the
    # output we already paid for, not a second run of it.
    primary_trail = audit.snapshot()

    for seed in extra_seeds:
        baseline = run_baseline(seed, days, verbose=False)
        agent = run_agent(seed, days, verbose=False)
        agent_ev = run_agent(seed, days, verbose=False, ev_mode=True) if primary_agent_ev is not None else None
        rows.append(row(seed, baseline, agent, agent_ev))

    generate.ensure_dataset(primary_seed)   # leave the dataset as we found it
    audit.restore(primary_trail)            # and the audit trail with it

    money_wins = sum(1 for r in rows if r["money_win"])
    days_eligible = [r for r in rows if r["matched_n"] > 0]
    days_wins = sum(1 for r in days_eligible if r["days_win"])
    summary = {
        "rows": rows,
        "money_win_rate": f"{money_wins}/{len(rows)}",
        "days_win_rate": f"{days_wins}/{len(days_eligible)}" if days_eligible else "n/a",
        "days_excluded": len(rows) - len(days_eligible),
    }
    if primary_agent_ev is not None:
        ev_wins = sum(1 for r in rows if r["agent_ev_money_win"])
        summary["agent_ev_money_win_rate"] = f"{ev_wins}/{len(rows)}"
    return summary


def _print_outcomes_file(path: Path) -> None:
    """Say what landed in the outcomes file, so its provenance is visible in
    the same output as the numbers it explains -- not discovered later by
    someone summing a file that turned out to hold something else."""
    found = outcomes.runs(path)
    rows = sum(r["action_rows"] + r["unattributed_rows"] for r in found.values())
    seeds = sorted({r["seed"] for r in found.values()})
    print(f"outcome attribution written to {path}")
    print(f"  {len(found)} run(s), {rows} rows, seeds {seeds}")


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
    parser.add_argument("--scenario", choices=("tc141",), default=None,
                        help="run a scripted end-to-end scenario instead of the seeded simulation "
                             "(docs/edge_cases.md TC-141 -> tc141)")
    args = parser.parse_args()

    if args.scenario == "tc141":
        # Imported here, not at module level: sim/scenario_tc141.py imports
        # _forced_mock_mode etc. back from this module, and importing it at
        # module level would be circular.
        from sim import scenario_tc141

        print(f"simulator: scenario={args.scenario}")
        result = scenario_tc141.run()
        print()
        for line in result["narrative"]:
            print(line)
        return 0

    print(f"simulator: seed={args.seed}, days={args.days}, "
          f"mode={'baseline vs agent vs agent+EV' if args.compare else 'agent only'}")

    # The ONE place the outcomes file is truncated (engine/outcomes.py's FILE
    # LIFECYCLE note). Deliberately here and not inside run_agent()/
    # run_baseline(): a --compare below runs those eighteen times and every
    # one of those runs belongs in this invocation's file. Deliberately after
    # the --scenario branch above, too, which returns without ever writing a
    # row and so has no business destroying the last real run's file.
    outcomes_path = outcomes.start_file()

    if args.compare:
        baseline = run_baseline(args.seed, args.days, verbose=args.verbose)
        agent = run_agent(args.seed, args.days, verbose=args.verbose)
        # agent's own audit trail is what everything downstream of here reads
        # from disk -- the report's audit excerpt, early warnings and trip
        # wires (report/build_report.py) and multi_seed_summary()'s own
        # "restore the primary seed's trail" both assume it. run_agent()
        # unconditionally clears and rewrites the shared trail on every call,
        # so without snapshotting it here, the agent+EV run immediately
        # below would silently become the trail everything else sees.
        agent_trail = audit.snapshot()
        # Phase 4's third arm: the same agent, with config/rules.yaml's
        # brain.ev_mode forced on -- the long-deferred ablation of whether
        # the negotiation layer (engine/negotiation.py, wired into
        # engine/brain.py in Phase 3) adds recovery on TOP of the
        # already-built agent, not just whether the agent beats a naive
        # baseline (which agent vs. baseline above already answers).
        agent_ev = run_agent(args.seed, args.days, verbose=args.verbose, ev_mode=True)
        audit.restore(agent_trail)
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
            print("-- agent+EV narrative --")
            for line in agent_ev["narrative"]:
                print(f"  {line}")
        print()
        _print_summary("baseline", baseline)
        print()
        _print_summary("agent (ev off)", agent)
        print()
        _print_summary("agent+EV (ev on)", agent_ev)
        gain = agent["final"]["recovered_paise"] - baseline["final"]["recovered_paise"]
        ev_gain = agent_ev["final"]["recovered_paise"] - agent["final"]["recovered_paise"]
        print()
        print(f"agent recovered {format_inr(gain, 'Rs ')} more than the baseline "
              f"with {agent['messages_sent'] - baseline['messages_sent']:+d} messages")
        print(f"agent+EV recovered {format_inr(abs(ev_gain), 'Rs ')} "
              f"{'more' if ev_gain >= 0 else 'less'} than agent (ev off) -- the ablation")
        matched = matched_avg_days_to_pay(baseline, agent)
        if matched["n"]:
            print(f"avg days to pay, {matched['n']} invoices BOTH recovered "
                  f"(the fair comparison): baseline {matched['baseline']}, agent {matched['agent']}")

        extra_seeds = tuple(int(s) for s in args.extra_seeds.split(",") if s.strip())
        multi_seed = None
        if extra_seeds:
            print()
            print(f"running {len(extra_seeds)} more seeds for the multi-seed table "
                  f"(baseline, agent, agent+EV): {extra_seeds}")
            multi_seed = multi_seed_summary(args.seed, baseline, agent, extra_seeds, args.days,
                                            primary_agent_ev=agent_ev)
            print(f"agent won on rupees recovered in {multi_seed['money_win_rate']} seeds, "
                  f"on avg days-to-pay (fair comparison) in {multi_seed['days_win_rate']} seeds")
            print(f"agent+EV beat agent (ev off) on rupees recovered in "
                  f"{multi_seed['agent_ev_money_win_rate']} seeds -- the ablation")

        _write_results(args.results_out, args.seed, args.days, baseline, agent, matched, multi_seed,
                       agent_ev=agent_ev)
        print(f"results written to {args.results_out}")
        _print_outcomes_file(outcomes_path)
    else:
        agent = run_agent(args.seed, args.days, verbose=args.verbose)
        if args.verbose:
            print()
            for line in agent["narrative"]:
                print(f"  {line}")
        print()
        _print_summary("agent", agent)
        _print_outcomes_file(outcomes_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
