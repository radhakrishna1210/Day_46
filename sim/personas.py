"""Buyer personas -- each fake buyer gets a hidden personality.

    The Forgetful       80% pay within 3 days of any reminder
    The Cash-Tight      ignores soft nudges; promises when pushed; keeps it 70%
    The Habitual Delayer only moves when interest/tax numbers appear (60% then)
    The Disputer        replies with a complaint about the goods; needs a human
    The Deadbeat        10% ever pay; the right answer is to stop early

The persona is hidden from the agent. It only drives how the simulated buyer
reacts, which is what makes the baseline-vs-agent experiment fair.

Day 8.
"""

from __future__ import annotations

from typing import Any

PERSONAS = ("forgetful", "cash_tight", "habitual_delayer", "disputer", "deadbeat")


def react(persona: str, message_rung: int, rng: Any) -> dict[str, Any]:
    """How this persona responds to a message at this rung.

    Returns a dict describing the reaction: pay / promise / dispute / silence,
    plus the free-text reply the promise tracker will have to parse.
    """
    raise NotImplementedError("step 8: buyer simulator")
