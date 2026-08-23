"""The brain -- picks exactly one action per invoice.

Inputs: buyer score, legal position, promise status, attempt counts.
Output: one rung on the bounded escalation ladder.

    Rung 0 WAIT | 1 SOFT NUDGE | 2 FIRM | 3 LEGAL FACTS | 4 STOP + HANDOFF
    any time: dispute detected -> human handoff immediately

Mostly rules. The LLM is consulted only for genuinely ambiguous cases, and its
reasoning is written to the audit trail. The decision to STOP is never made by
the LLM -- stopping rules are enforced here, in code.

Day 6.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def decide(case: dict[str, Any], today: date) -> dict[str, Any]:
    """Choose the next action for one case.

    Returns a dict with the chosen rung, the reason, and whether the reason
    came from a rule or from the LLM -- both go straight to the audit trail.
    """
    raise NotImplementedError("step 4: brain")


def stop_reason(case: dict[str, Any], today: date) -> str | None:
    """Return why we must not send anything, or None if sending is allowed.

    Hard limits: max messages per rung, max total per invoice, quiet hours,
    opt-out, open dispute.
    """
    raise NotImplementedError("step 4: brain")
