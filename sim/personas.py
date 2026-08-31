"""Buyer personas -- each fake buyer gets a hidden personality.

    The Forgetful       pays promptly, most of the time after a single nudge
    The Cash-Tight      ignores soft nudges; promises when pushed; keeps it 70%
    The Habitual Delayer only moves when interest/tax numbers appear
    The Disputer        replies with a complaint about the goods; needs a human
    The Deadbeat        almost never pays; the right answer is to stop early

The persona is hidden from the agent: it lives only in sim/hidden_personas.json,
written by data/generate.py and read only here. tests/test_sim_isolation.py
proves nothing under engine/ (or main.py) can reach it.

react() decides, for one message just sent at one rung, which of five things
the buyer does: pay in full, pay part of it, promise to pay, dispute the
invoice, or say nothing. A promise or a dispute is turned into the same kind
of free-text reply a real buyer would send, using one of the fixtures already
defined in config/replies.yaml, so it can run through the real
engine.promises.parse_reply -> apply_reply path rather than being applied as
a shortcut. Paying is ground truth, not language -- it is applied directly by
the caller from the amount this module reports.

REACTION_TABLE and PROMISE_KEEP_CHANCE are simulator tuning knobs, not a
business rule -- like data/generate.py's PERSONA_BEHAVIOUR, they describe the
test harness's fake world, not anything engine/ is audited against, so they
stay as module constants here rather than in config/rules.yaml.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

PERSONAS = ("forgetful", "cash_tight", "habitual_delayer", "disputer", "deadbeat")

#: The five things a buyer can do in response to one message.
PAY_FULL, PAY_PARTIAL, PROMISE, DISPUTE, SILENCE = (
    "pay_full", "pay_partial", "promise", "dispute", "silence",
)
OUTCOMES = (PAY_FULL, PAY_PARTIAL, PROMISE, DISPUTE, SILENCE)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HIDDEN_PERSONAS_PATH = ROOT / "sim" / "hidden_personas.json"

#: persona -> rung -> outcome -> probability. Rung 1 is the courtesy nudge,
#: 2 is firm-with-interest, 3 adds the tax and disclosure facts. Rows sum to
#: 1.0 (checked by tests/test_personas.py, not just asserted here).
REACTION_TABLE: dict[str, dict[int, dict[str, float]]] = {
    "forgetful": {
        1: {PAY_FULL: 0.55, PAY_PARTIAL: 0.10, PROMISE: 0.15, DISPUTE: 0.00, SILENCE: 0.20},
        2: {PAY_FULL: 0.74, PAY_PARTIAL: 0.10, PROMISE: 0.10, DISPUTE: 0.00, SILENCE: 0.06},
        3: {PAY_FULL: 0.85, PAY_PARTIAL: 0.05, PROMISE: 0.05, DISPUTE: 0.00, SILENCE: 0.05},
    },
    "cash_tight": {
        1: {PAY_FULL: 0.05, PAY_PARTIAL: 0.05, PROMISE: 0.10, DISPUTE: 0.00, SILENCE: 0.80},
        2: {PAY_FULL: 0.10, PAY_PARTIAL: 0.15, PROMISE: 0.50, DISPUTE: 0.00, SILENCE: 0.25},
        3: {PAY_FULL: 0.15, PAY_PARTIAL: 0.20, PROMISE: 0.45, DISPUTE: 0.00, SILENCE: 0.20},
    },
    "habitual_delayer": {
        1: {PAY_FULL: 0.00, PAY_PARTIAL: 0.00, PROMISE: 0.05, DISPUTE: 0.00, SILENCE: 0.95},
        2: {PAY_FULL: 0.10, PAY_PARTIAL: 0.10, PROMISE: 0.29, DISPUTE: 0.00, SILENCE: 0.51},
        3: {PAY_FULL: 0.20, PAY_PARTIAL: 0.15, PROMISE: 0.37, DISPUTE: 0.00, SILENCE: 0.28},
    },
    "disputer": {
        1: {PAY_FULL: 0.00, PAY_PARTIAL: 0.00, PROMISE: 0.05, DISPUTE: 0.60, SILENCE: 0.35},
        2: {PAY_FULL: 0.00, PAY_PARTIAL: 0.00, PROMISE: 0.00, DISPUTE: 0.74, SILENCE: 0.26},
        3: {PAY_FULL: 0.00, PAY_PARTIAL: 0.00, PROMISE: 0.00, DISPUTE: 0.85, SILENCE: 0.15},
    },
    "deadbeat": {
        1: {PAY_FULL: 0.01, PAY_PARTIAL: 0.01, PROMISE: 0.03, DISPUTE: 0.02, SILENCE: 0.93},
        2: {PAY_FULL: 0.02, PAY_PARTIAL: 0.02, PROMISE: 0.05, DISPUTE: 0.03, SILENCE: 0.88},
        3: {PAY_FULL: 0.03, PAY_PARTIAL: 0.02, PROMISE: 0.05, DISPUTE: 0.05, SILENCE: 0.85},
    },
}

#: Chance a kept-or-broken promise is actually kept, checked once at maturity.
PROMISE_KEEP_CHANCE: dict[str, float] = {
    "forgetful": 0.95,
    "cash_tight": 0.70,
    "habitual_delayer": 0.40,
    "disputer": 0.40,
    "deadbeat": 0.15,
}

#: config/replies.yaml fixture keys, grouped by the intent they carry. Picking
#: from these keeps mock-mode fully deterministic with no new LLM plumbing --
#: the reply text a persona "says" is one already defined and reviewed there.
PROMISE_VARIANTS: tuple[str, ...] = (
    "promise_tarikh_hinglish",
    "promise_explicit_english",
    "promise_partial_hinglish",
    "promise_month_end",
)
DISPUTE_VARIANTS: tuple[str, ...] = (
    "dispute_damage_hinglish",
    "dispute_po_mismatch",
)

#: Phase 4: what kind of action the message just sent was, per
#: engine.brain's own kinds (SEND maps to "send" here; PAYMENT_PLAN and
#: COUNTER_SETTLE keep their own names). "send" is the default and is the
#: ONLY value every pre-Phase-4 call site implicitly used -- see react()'s
#: own docstring for the backward-compatibility guarantee that depends on it.
ACTION_KINDS = ("send", "payment_plan", "counter_settle")

#: Persona -> how many points of PROMISE probability to take from SILENCE
#: when offered a payment_plan instead of a plain send, at every rung. Only
#: personas whose behaviour already correlates with genuine cash-flow
#: constraint (data/generate.py's PERSONA_BEHAVIOUR: negative inflow drift,
#: high inflow volatility, real failed-payment history) get a boost --
#: cash_tight is the one persona built that way. This is the real-world
#: intuition config/rules.yaml's negotiation.eligible_actions is already
#: built on (payment_plan is offered to cash_flow_problem, not to
#: can_pay_but_wont/high_risk): a buyer who wants to pay but cannot on the
#: original schedule should engage meaningfully MORE when the obstacle
#: (timing) is actually addressed. forgetful (good_customer) and
#: habitual_delayer/disputer (can_pay_but_wont) are deliberately absent: a
#: good payer accepting a plan they did not need is not a meaningful signal,
#: and a buyer who is unwilling rather than unable has no more reason to
#: promise for a schedule than for a plain ask. deadbeat (high_risk) is
#: absent because payment_plan is not even offered to that quadrant.
PAYMENT_PLAN_PROMISE_BOOST: dict[str, float] = {
    "cash_tight": 0.25,
}

#: Persona -> chance that a PROMISE produced in response to a counter_settle
#: offer is drawn specifically from the reduced-terms fixture
#: (promise_partial_hinglish, which engine.promises.parse_reply() resolves
#: to amount="partial" -- a genuine partial payment when the promise is
#: later kept, via sim/run_sim.py's _advance_promises()) rather than
#: uniformly from PROMISE_VARIANTS. Represents "continued lowballing" --
#: accepted, but for less than proposed -- reusing the existing partial-
#: promise mechanic rather than inventing a new outcome category, exactly
#: as this phase's brief asks. Only can_pay_but_wont's persona
#: (habitual_delayer) gets this: counter_settle is not offered to
#: good_customer/cash_flow_problem at all, and disputer's own reaction table
#: is already dominated by DISPUTE, not PROMISE, so there is nothing
#: meaningful to bias there. The remaining chance still falls back to a
#: uniform draw over PROMISE_VARIANTS, so a full-terms promise stays
#: possible, just not favoured.
#: 0.70, not 0.75 -- tests/test_no_legal_constants.py bans 0.75 repo-wide
#: (it is config/legal.yaml's Samadhaan pre-deposit share), and this number
#: has nothing to do with that: a coincidental collision in VALUE only, not
#: in meaning, so the fix is picking a different number, not an exception.
COUNTER_SETTLE_PARTIAL_BIAS: dict[str, float] = {
    "habitual_delayer": 0.70,
}

#: The one PROMISE_VARIANTS entry that resolves to a partial promise -- see
#: COUNTER_SETTLE_PARTIAL_BIAS above.
_REDUCED_TERMS_VARIANT = "promise_partial_hinglish"


def _boost_promise(table: dict[str, float], boost: float) -> dict[str, float]:
    """Shift `boost` points of probability from SILENCE to PROMISE.

    The row still sums to 1.0 either way -- boost is clamped to what SILENCE
    actually has to give, so this can never push another outcome negative.
    """
    boost = min(boost, table[SILENCE])
    return {**table, PROMISE: table[PROMISE] + boost, SILENCE: table[SILENCE] - boost}


def load_hidden_personas(path: Path | None = None) -> dict[str, str]:
    """buyer_id -> persona tag, from sim/hidden_personas.json.

    The only reader of this file anywhere in the codebase -- engine/ must
    infer buyer behaviour from payment history, never from this file.
    """
    payload = json.loads((path or DEFAULT_HIDDEN_PERSONAS_PATH).read_text(encoding="utf-8"))
    return dict(payload["personas"])


def _fixture_reply(key: str) -> str:
    from engine.config import replies

    fixtures = {item["key"]: item for item in replies()["fixtures"]}
    return str(fixtures[key]["reply"])


def react(
    persona: str, message_rung: int, rng: random.Random, *, action_kind: str = "send",
) -> dict[str, Any]:
    """How this persona responds to a message sent at this rung.

    Args:
        persona: one of PERSONAS.
        message_rung: the rung of the message just sent (1, 2 or 3 -- rung 0
            and 4 never reach a buyer, so callers never ask about them).
            payment_plan/counter_settle always inherit an existing send-tier
            rung too (engine/brain.py never assigns them rung 4), so this
            bound holds for every action_kind.
        rng: seeded per (invoice, day) by the caller, so the same buyer facing
            the same message on the same simulated day gets the same roll
            whether this is the baseline run or the agent run.
        action_kind: one of ACTION_KINDS. "send" (the default) reproduces
            EVERY pre-Phase-4 call site's behaviour exactly -- this parameter
            only ever changes the outcome for "payment_plan" (cash_tight gets
            a real promise-rate boost; see PAYMENT_PLAN_PROMISE_BOOST) and
            "counter_settle" (habitual_delayer's promises skew toward
            reduced terms; see COUNTER_SETTLE_PARTIAL_BIAS). Every other
            persona/action_kind combination -- including a payment_plan or
            counter_settle reaching a persona with no configured
            differentiation, which should not normally happen since
            config/rules.yaml's negotiation.eligible_actions already keeps
            each action within its intended quadrant, but is not itself
            invalid here -- behaves exactly like "send".

    Returns:
        outcome: one of OUTCOMES.
        For PROMISE or DISPUTE: reply (free text) and variant (the
        config/replies.yaml fixture key it came from), ready for
        engine.promises.parse_reply(text, today, variant=variant).
    """
    if persona not in REACTION_TABLE:
        raise ValueError(f"unknown persona {persona!r}; expected one of {PERSONAS}")
    if message_rung not in (1, 2, 3):
        raise ValueError(f"personas only react to buyer-facing rungs (1-3), got {message_rung!r}")
    if action_kind not in ACTION_KINDS:
        raise ValueError(f"unknown action_kind {action_kind!r}; expected one of {ACTION_KINDS}")

    table = REACTION_TABLE[persona][message_rung]
    if action_kind == "payment_plan":
        boost = PAYMENT_PLAN_PROMISE_BOOST.get(persona, 0.0)
        if boost:
            table = _boost_promise(table, boost)
    outcomes = list(table)
    weights = [table[o] for o in outcomes]
    outcome = rng.choices(outcomes, weights=weights, k=1)[0]

    result: dict[str, Any] = {"outcome": outcome}
    if outcome == PROMISE:
        # `bias and rng.random() < bias`, not `rng.random() < bias` alone:
        # a persona with no configured bias (or action_kind != counter_settle)
        # must not consume an rng draw here at all, or its variant choice
        # would silently differ from the plain-"send" path even though the
        # OUTCOME (which branch runs) never would have changed either way.
        bias = COUNTER_SETTLE_PARTIAL_BIAS.get(persona, 0.0) if action_kind == "counter_settle" else 0.0
        if bias and rng.random() < bias:
            variant = _REDUCED_TERMS_VARIANT
        else:
            variant = rng.choice(PROMISE_VARIANTS)
        result["reply"] = _fixture_reply(variant)
        result["variant"] = variant
    elif outcome == DISPUTE:
        variant = rng.choice(DISPUTE_VARIANTS)
        result["reply"] = _fixture_reply(variant)
        result["variant"] = variant
    return result


def keeps_promise(persona: str, rng: random.Random) -> bool:
    """Whether a promise maturing today is kept, once, at maturity."""
    chance = PROMISE_KEEP_CHANCE.get(persona, 0.5)
    return rng.random() < chance
