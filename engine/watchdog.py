"""Watchdog -- finds the invoices that have gone overdue. Pure rules, date math.

Runs once per simulated day. The clock is always passed in, never read from
date.today(), so tests and the simulator can time-travel.

Day 3.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def overdue_invoices(invoices: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Return the unpaid invoices whose due date is behind today."""
    raise NotImplementedError("step 1: watchdog")


def due_promises(promises: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Return promises whose promised date has passed without payment."""
    raise NotImplementedError("step 1: watchdog")
