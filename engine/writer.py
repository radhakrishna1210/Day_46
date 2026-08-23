"""Message writer -- the AI writes the actual words. Calls engine.llm only.

Receives the rung, the buyer profile (corporate vs small trader), the language
preference (English / Hinglish), every law number, and the promise history.

Guardrail: output must pass a checklist before it can be sent -- no threats, no
invented facts, and every number must match the law engine exactly.

Day 6.
"""

from __future__ import annotations

from typing import Any


def draft(case: dict[str, Any], rung: int) -> str:
    """Draft the message for this case at this rung."""
    raise NotImplementedError("step 5: message writer")


def passes_guardrail(message: str, case: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check a draft against the guardrail checklist.

    Returns (ok, failures). A message that fails is never sent.
    """
    raise NotImplementedError("step 5: message writer")
