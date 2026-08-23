"""Audit trail -- the append-only record of every money-related action.

Non-negotiable #1: no silent actions. Every entry carries a timestamp, the
invoice, the action, the reason, and whether the reason came from a rule or
from the AI. Logs land in audit/.

Day 1 stub; wired up as each block lands.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

Source = Literal["rule", "ai"]


def record(
    invoice_id: str,
    action: str,
    reason: str,
    source: Source,
    today: date,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one action to the audit trail."""
    raise NotImplementedError("audit trail")


def entries_for(invoice_id: str) -> list[dict[str, Any]]:
    """Every recorded action for one invoice, oldest first."""
    raise NotImplementedError("audit trail")
