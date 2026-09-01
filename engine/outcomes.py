"""Outcome attribution -- which action actually got the money in?

The audit trail (engine/audit.py) answers "what did we do, and why?". It says
nothing about whether any of it WORKED. This module answers the next question,
per action: did a payment land close enough behind this action to plausibly be
its result, and how much?

SIMULATOR ONLY. Nothing here is imported by engine/brain.py, and no decision
anywhere reads a record this module writes. It observes; it never steers. That
separation is deliberate: an attribution number is a claim about cause, and a
claim about cause built on a 120-day toy world has no business moving real
money until it has been argued over first -- the same "ship the reasoning
inert, then wire it" discipline engine/negotiation.py was built under.

THE ATTRIBUTION RULE, exactly as specified, and the only one implemented:

  * Credit for a payment goes to the MOST RECENT action on that invoice
    within config/rules.yaml's learning.attribution_horizon_days BEFORE the
    payment date. Same-day counts (a persona reacting to today's message pays
    today, distance 0). Each payment is credited to at most ONE action.
  * An action with no payment inside its horizon is a recorded FAILURE --
    paid_within_horizon false, paise_recovered_within_horizon 0. Failures are
    written out, not omitted: a file of nothing but successes would make every
    action look like it worked.
  * A payment with no action preceding it inside the horizon is recorded as
    UNATTRIBUTED and counted separately. It is never silently dropped and
    never quietly folded into the nearest action -- a buyer who pays on their
    own is real, and pretending we caused it is exactly the dishonesty this
    project's non-negotiable #5 is about.

WHAT COUNTS AS "AN ACTION THE SIMULATOR EXECUTED" is the caller's call, not
this module's -- sim/run_sim.py records outbound contacts and human handoffs
and deliberately does NOT record waits or stops. See its own comments for why
(short version: crediting a payment to a decision to stop chasing would
poison the very signal this file exists to produce).

KNOWN, STATED LIMITATION: proximity is not causation. A buyer inside their own
payment cycle who would have paid anyway still credits whichever message
happened to land first. This is a correlational attribution window -- the same
one every marketing attribution model uses -- and saying so here is the honest
version.

Output: audit/outcomes.jsonl, one JSON object per line, gitignored alongside
the audit trail.

FILE LIFECYCLE -- read this before analysing the file. Truncation is explicit
and happens in exactly ONE place: sim/run_sim.py's main() calls start_file()
once, before the first run. write() itself only ever appends. So:

    one `python sim/run_sim.py ...` invocation  ==  one self-consistent file

and everything that invocation runs accumulates into it -- baseline, agent and
agent+EV, once per seed, all 18 run groups of a --compare. Accumulation WITHIN
an invocation is the deliberate part (a multi-seed comparison is one
experiment, and its arms belong in one file); accumulation ACROSS invocations
is not, and start_file() is what prevents it.

This replaced an earlier truncate-on-first-write-per-process rule, which was
implicit and got it wrong in a way worth recording: a pytest process calling
run_agent() several times stacked every one of those runs into the production
file, silently, and the resulting file looked exactly like a real --compare
until someone summed it. Two fixes, because either alone leaves a hole -- the
explicit start_file() above, and conftest.py pointing OUTCOMES_PATH at a
throwaway file for the whole test session, so the suite cannot write the
production artifact at all.

Every record additionally carries `run_id` (see OutcomeLedger.run_id), which
identifies the single run that produced it even if files from separate
invocations are later concatenated on purpose -- which Day 4's multi-seed work
is expected to do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from engine.config import rules

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES_PATH = ROOT / "audit" / "outcomes.jsonl"

#: Line kinds in outcomes.jsonl. Every line carries one of these under
#: "record_type", so a reader never has to guess from the keys present.
ACTION_RECORD = "action"
UNATTRIBUTED_PAYMENT_RECORD = "unattributed_payment"

#: Every run_id handed out in this process, so two ledgers built inside the
#: same clock tick still get distinct ids. Normally never consulted -- see
#: _new_run_id().
_issued_run_ids: set[str] = set()


def _resolve(path: Path | None) -> Path:
    """OUTCOMES_PATH, read at CALL time rather than bound as a default.

    conftest.py redirects the whole test session away from the production
    file by reassigning OUTCOMES_PATH; a default argument would have captured
    the original at import time and quietly ignored that.
    """
    return OUTCOMES_PATH if path is None else path


def _new_run_id(seed: int, mode: str) -> str:
    """``{seed}_{mode}_{timestamp}`` -- provenance for one run's rows.

    WALL CLOCK, deliberately, and the one place in this codebase that uses it.
    engine/audit.py bans datetime.now() from its entries for a good reason (a
    trail that changes between two identical runs cannot be diffed), and that
    reasoning still holds -- which is why run_id goes on the JSONL rows only
    and never into the summary a run returns to its caller. A record of WHAT
    the agent did must be reproducible; a label saying WHICH execution wrote
    these bytes has the opposite job, and has to differ between two otherwise
    identical runs or it cannot separate them.
    """
    base = f"{seed}_{mode}_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
    run_id, bump = base, 1
    while run_id in _issued_run_ids:
        bump += 1
        run_id = f"{base}-{bump}"
    _issued_run_ids.add(run_id)
    return run_id


def horizon_days(config: dict[str, Any] | None = None) -> int:
    """config/rules.yaml's learning.attribution_horizon_days. Never hardcoded."""
    settings = config if config is not None else rules()
    return int(settings["learning"]["attribution_horizon_days"])


@dataclass(frozen=True)
class ActionEvent:
    """One action the simulator executed, before anyone knows if it worked."""

    invoice_id: str
    buyer_id: str
    day: date
    quadrant: str | None
    #: What was ACTUALLY EXECUTED -- engine.brain's own Action.kind and the
    #: rung the message really went out at. Under exploration these can
    #: differ from what was proposed (see proposed_action_kind below); when
    #: they do, this pair is the one that tells the truth about what the
    #: buyer received, and so the one an outcome may be credited to.
    action_kind: str
    rung: int
    outstanding_paise_at_action: int
    #: What the selection policy PROPOSED, in engine.negotiation's action
    #: space, before the escalation walk and the law ceiling had their say --
    #: None for any run with no EV/exploration selection behind it (the
    #: baseline, and the plain agent arm). proposed_rung is the ladder rung
    #: that proposal named, or None for an action that names no rung of its
    #: own (see engine.brain.negotiation_rung).
    proposed_action_kind: str | None
    proposed_rung: int | None
    #: True when the proposal named a rung and a different one was executed --
    #: i.e. the gates overrode the label. Directly countable across a file,
    #: which is the whole reason the proposal is kept alongside the outcome.
    gate_override: bool
    #: Strictly increasing over the whole ledger. Two actions on the same
    #: invoice on the same day are ordered by this, so "most recent" is always
    #: a total order rather than a tie the code resolves by luck.
    seq: int


@dataclass(frozen=True)
class PaymentEvent:
    """Money that actually landed, as recorded by the simulator's own ledger."""

    invoice_id: str
    day: date
    amount_paise: int
    seq: int


class OutcomeLedger:
    """Collects actions and payments during a run, attributes at the end.

    Attribution is deliberately deferred to :meth:`attribute` rather than
    decided as each payment arrives: an action's verdict is not knowable until
    its whole horizon has elapsed, and a run that judged as it went would have
    to revise. Collecting first and judging once keeps the rule readable and
    keeps the answer independent of the order the simulator happened to call
    us in.
    """

    def __init__(self, *, mode: str, seed: int, horizon: int | None = None,
                 run_id: str | None = None) -> None:
        """Args:
            mode: which arm produced these rows -- "baseline", "agent",
                "agent_ev". Goes on every record.
            seed: the seed the simulator was ACTUALLY run with, passed down
                from run_agent()/run_baseline()'s own argument rather than
                re-read from the dataset on disk, which a concurrent run could
                have replaced underneath us.
            horizon: attribution window in days; defaults to config.
            run_id: normally derived (see _new_run_id) -- supplied only by
                tests that need a stable, readable id.
        """
        self.mode = mode
        self.seed = int(seed)
        self.run_id = run_id if run_id is not None else _new_run_id(seed, mode)
        self.horizon = horizon_days() if horizon is None else int(horizon)
        self.actions: list[ActionEvent] = []
        self.payments: list[PaymentEvent] = []
        self._seq = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record_action(
        self,
        *,
        invoice_id: str,
        buyer_id: str,
        day: date,
        action_kind: str,
        rung: int,
        outstanding_paise_at_action: int,
        quadrant: str | None = None,
        proposed_action_kind: str | None = None,
        proposed_rung: int | None = None,
        gate_override: bool = False,
    ) -> ActionEvent:
        """Note one executed action.

        `action_kind`/`rung` are always what was ACTUALLY EXECUTED. That is
        the invariant the attribution rests on: a payment is credited to what
        the buyer actually received, never to a label that a gate overrode
        before the message went out.

        `quadrant` is None when the run has no two-axis score (the baseline
        bot has none) -- written out as null, never guessed at. The three
        proposal fields are likewise None/False for any run with no EV or
        exploration selection behind it, rather than being invented.
        """
        event = ActionEvent(
            invoice_id=invoice_id,
            buyer_id=buyer_id,
            day=day,
            quadrant=quadrant,
            action_kind=action_kind,
            rung=int(rung),
            outstanding_paise_at_action=int(outstanding_paise_at_action),
            proposed_action_kind=proposed_action_kind,
            proposed_rung=None if proposed_rung is None else int(proposed_rung),
            gate_override=bool(gate_override),
            seq=self._next_seq(),
        )
        self.actions.append(event)
        return event

    def record_payment(self, *, invoice_id: str, day: date, amount_paise: int) -> PaymentEvent:
        """Note money that landed.

        Called with the amount ACTUALLY applied (post-clamp), so the ledger
        can never credit an action with more than the buyer really paid.
        """
        event = PaymentEvent(
            invoice_id=invoice_id,
            day=day,
            amount_paise=int(amount_paise),
            seq=self._next_seq(),
        )
        self.payments.append(event)
        return event

    # ----------------------------------------------------------------------
    # the rule
    # ----------------------------------------------------------------------

    def attribute(self) -> dict[str, Any]:
        """Apply the attribution rule. Returns the records plus the totals.

        Returns:
            ``{"records": [...], "unattributed": [...], "summary": {...}}``.
            ``records`` holds one dict per recorded action, successes and
            failures alike, in the order the actions were recorded.
            ``unattributed`` holds one dict per payment no action could claim.
        """
        by_invoice: dict[str, list[ActionEvent]] = {}
        for action in self.actions:
            by_invoice.setdefault(action.invoice_id, []).append(action)

        credited_count: dict[int, int] = {action.seq: 0 for action in self.actions}
        credited_paise: dict[int, int] = {action.seq: 0 for action in self.actions}
        unattributed: list[dict[str, Any]] = []

        for payment in sorted(self.payments, key=lambda p: (p.day, p.seq)):
            winner = self._most_recent_action_within_horizon(
                by_invoice.get(payment.invoice_id, []), payment)
            if winner is None:
                unattributed.append({
                    "record_type": UNATTRIBUTED_PAYMENT_RECORD,
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "seed": self.seed,
                    "invoice_id": payment.invoice_id,
                    "day": payment.day.isoformat(),
                    "amount_paise": payment.amount_paise,
                    "reason": (f"no action on this invoice within {self.horizon} "
                               f"day(s) before the payment"),
                })
                continue
            credited_count[winner.seq] += 1
            credited_paise[winner.seq] += payment.amount_paise

        records = [{
            "record_type": ACTION_RECORD,
            "run_id": self.run_id,
            "mode": self.mode,
            "seed": self.seed,
            "invoice_id": action.invoice_id,
            "buyer_id": action.buyer_id,
            "day": action.day.isoformat(),
            "quadrant": action.quadrant,
            "action_kind": action.action_kind,
            "rung": action.rung,
            "outstanding_paise_at_action": action.outstanding_paise_at_action,
            "proposed_action_kind": action.proposed_action_kind,
            "proposed_rung": action.proposed_rung,
            "gate_override": action.gate_override,
            "paid_within_horizon": credited_count[action.seq] > 0,
            "paise_recovered_within_horizon": credited_paise[action.seq],
        } for action in self.actions]

        # No run_id here, on purpose: this dict is returned to the caller and
        # ends up in each arm's report (and so in results.json), where
        # tests/test_run_sim.py requires two identical runs to compare equal.
        # A wall-clock id would break that guarantee to buy nothing -- mode +
        # seed already identify the run deterministically, and anyone needing
        # to point at the exact rows has ledger.run_id.
        summary = {
            "mode": self.mode,
            "seed": self.seed,
            "attribution_horizon_days": self.horizon,
            "actions_recorded": len(records),
            "actions_paid_within_horizon": sum(1 for r in records if r["paid_within_horizon"]),
            "payments_recorded": len(self.payments),
            "payments_attributed": len(self.payments) - len(unattributed),
            "payments_unattributed": len(unattributed),
            "paise_attributed": sum(credited_paise.values()),
            "paise_unattributed": sum(p["amount_paise"] for p in unattributed),
        }
        return {"records": records, "unattributed": unattributed, "summary": summary}

    def _most_recent_action_within_horizon(
        self, candidates: list[ActionEvent], payment: PaymentEvent,
    ) -> ActionEvent | None:
        """The latest action at or before the payment, no older than the horizon.

        ``action.seq < payment.seq`` is what keeps a same-day action that was
        recorded AFTER the payment (a message sent to a buyer who had already
        paid that morning) from claiming credit for it.
        """
        eligible = [
            action for action in candidates
            if 0 <= (payment.day - action.day).days <= self.horizon
            and action.seq < payment.seq
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda action: (action.day, action.seq))

    # ----------------------------------------------------------------------
    # output
    # ----------------------------------------------------------------------

    def write(self, path: Path | None = None) -> dict[str, Any]:
        """Attribute, APPEND every line to `path`, and return what
        :meth:`attribute` returned. Unattributed payments are written too.

        Appends. It never truncates -- see start_file() and the module
        docstring's FILE LIFECYCLE note for where that decision lives.
        """
        result = self.attribute()
        write(result["records"] + result["unattributed"], path=path)
        return result


def start_file(path: Path | None = None) -> Path:
    """Begin a fresh outcomes file. The ONE place truncation happens.

    Called once by sim/run_sim.py's main(), before the first run of an
    invocation, so that invocation's runs accumulate into a file containing
    nothing else. Deliberately NOT called by run_agent()/run_baseline():
    a single --compare runs those eighteen times and each one clearing the
    file would leave only the last, which is the mirror-image of the bug this
    replaced.
    """
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return target


def write(lines: list[dict[str, Any]], path: Path | None = None) -> None:
    """Append JSONL. Never truncates -- start_file() is what does that."""
    target = _resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=False) + "\n")


def records(path: Path | None = None) -> list[dict[str, Any]]:
    """Every line in the file, oldest first."""
    target = _resolve(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def runs(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """What the file actually holds, one entry per run_id.

    The check anyone analysing outcomes.jsonl should run first -- today's
    confusion (see the module docstring) would have been visible immediately
    in this output, as five run_ids for a --compare that should have had
    eighteen.
    """
    found: dict[str, dict[str, Any]] = {}
    for row in records(path):
        entry = found.setdefault(row["run_id"], {
            "run_id": row["run_id"], "mode": row["mode"], "seed": row["seed"],
            "action_rows": 0, "unattributed_rows": 0,
        })
        key = ("action_rows" if row["record_type"] == ACTION_RECORD
               else "unattributed_rows")
        entry[key] += 1
    return found


def gate_overrides(path: Path | None = None,
                   mode: str | None = None) -> dict[str, Any]:
    """How often the gates overrode the action the selection policy proposed.

    The measurement sim/run_sim.py's exploration mode exists to produce, kept
    here as a reader rather than folded into OutcomeLedger.attribute()'s
    summary on purpose: that summary is returned to each arm's report and so
    lands in results.json, which tests/test_run_sim.py requires to be
    byte-reproducible across identical runs. Adding always-zero keys to every
    baseline row's summary to serve one experimental arm would churn a
    committed artifact for nothing.

    Only rows that CARRY a proposal count -- the baseline and the plain agent
    arm propose nothing, so counting their rows in the denominator would
    understate the rate rather than merely dilute it.

    Args:
        path: outcomes file; defaults to OUTCOMES_PATH.
        mode: restrict to one arm ("agent_ev_explore", say). None reads all.

    Returns:
        ``{"actions_with_proposal", "gate_overrides", "rate",
        "by_proposed_action"}``. ``rate`` is None when nothing proposed
        anything, rather than a 0 that would read as "never overridden".
    """
    rows = [r for r in records(path)
            if r["record_type"] == ACTION_RECORD
            and r.get("proposed_action_kind") is not None
            and (mode is None or r["mode"] == mode)]
    by_action: dict[str, dict[str, int]] = {}
    for row in rows:
        entry = by_action.setdefault(row["proposed_action_kind"], {"proposed": 0, "overridden": 0})
        entry["proposed"] += 1
        if row.get("gate_override"):
            entry["overridden"] += 1
    overridden = sum(entry["overridden"] for entry in by_action.values())
    return {
        "actions_with_proposal": len(rows),
        "gate_overrides": overridden,
        "rate": (overridden / len(rows)) if rows else None,
        "by_proposed_action": dict(sorted(by_action.items())),
    }


def clear(path: Path | None = None) -> None:
    """Delete the file outright. For tests and for a clean slate."""
    target = _resolve(path)
    if target.exists():
        target.unlink()
