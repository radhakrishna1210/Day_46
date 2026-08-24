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


def react(persona: str, message_rung: int, rng: random.Random) -> dict[str, Any]:
    """How this persona responds to a message sent at this rung.

    Args:
        persona: one of PERSONAS.
        message_rung: the rung of the message just sent (1, 2 or 3 -- rung 0
            and 4 never reach a buyer, so callers never ask about them).
        rng: seeded per (invoice, day) by the caller, so the same buyer facing
            the same message on the same simulated day gets the same roll
            whether this is the baseline run or the agent run.

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

    table = REACTION_TABLE[persona][message_rung]
    outcomes = list(table)
    weights = [table[o] for o in outcomes]
    outcome = rng.choices(outcomes, weights=weights, k=1)[0]

    result: dict[str, Any] = {"outcome": outcome}
    if outcome == PROMISE:
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
