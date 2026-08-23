"""Promise tracker -- reads buyer replies and remembers what they committed to.

Real AI work: messy Hinglish/English free text into structure.

    "boss thoda time do, 5 tarikh tak ho jayega"
        -> {intent: promise, date: 2026-09-05, amount: full}

Also classifies: promise / dispute / refusal / question / noise. A detected
dispute stops the chase immediately and hands off to a human.

Day 7.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def parse_reply(text: str, today: date) -> dict[str, Any]:
    """Turn a free-text buyer reply into a structured intent, via engine.llm."""
    raise NotImplementedError("step 6: promise tracker")


def record_promise(invoice_id: str, parsed: dict[str, Any], today: date) -> dict[str, Any]:
    """Store a promise so the watchdog can catch it if it breaks."""
    raise NotImplementedError("step 6: promise tracker")


def is_broken(promise: dict[str, Any], today: date) -> bool:
    """True if the promised date has passed and the money did not arrive."""
    raise NotImplementedError("step 6: promise tracker")
