"""Audit trail -- the append-only record of every money-related action.

Non-negotiable #1: no silent actions. Every entry carries the simulation
timestamp, the invoice, who acted, what they did, why, and whether the reason
came from a rule or from the AI.

Two deliberate choices:

  * the timestamp is the SIMULATION clock, never datetime.now(). A run has to
    be reproducible, and a log that changes between two identical runs cannot
    be diffed or trusted.
  * one JSON object per line, appended. No rewriting, no database. A human can
    read it with `tail`, and a broken run leaves everything written so far.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "audit" / "audit_log.jsonl"

Source = Literal["rule", "llm"]

#: Dry runs decide exactly as a real run would, but write nothing.
_enabled = True


def enable() -> None:
    _enabled = True
    globals()["_enabled"] = _enabled


def disable() -> None:
    """Used by --dry-run: decide, print, record nothing."""
    globals()["_enabled"] = False


def is_enabled() -> bool:
    return _enabled


def _timestamp(when: date | datetime) -> str:
    """ISO timestamp from the simulation clock, midnight for a bare date."""
    if isinstance(when, datetime):
        return when.isoformat(timespec="seconds")
    return datetime(when.year, when.month, when.day).isoformat(timespec="seconds")


def record(
    invoice_id: str,
    action: str,
    reason: str,
    source: Source,
    today: date | datetime,
    *,
    buyer_id: str | None = None,
    actor: str = "brain",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append one action to the audit trail. Returns the entry, or None if off."""
    entry: dict[str, Any] = {
        "ts": _timestamp(today),
        "invoice_id": invoice_id,
        "buyer_id": buyer_id,
        "actor": actor,
        "action": action,
        "reason": reason,
        "source": source,
    }
    if detail:
        entry["detail"] = detail

    if not _enabled:
        return None

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=False) + "\n")
    return entry


def entries() -> list[dict[str, Any]]:
    """Every recorded action, oldest first."""
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def entries_for(invoice_id: str) -> list[dict[str, Any]]:
    """Every recorded action for one invoice, oldest first."""
    return [entry for entry in entries() if entry.get("invoice_id") == invoice_id]


def clear() -> None:
    """Delete the log. For tests and for starting a fresh simulation run."""
    if LOG_PATH.exists():
        LOG_PATH.unlink()
