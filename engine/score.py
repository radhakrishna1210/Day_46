"""Buyer score -- payment history compressed into one explainable number.

Rules, never AI: a score that drives money decisions has to be auditable.
Weights come from config/rules.yaml.

Day 3. Needs pytest coverage before it is marked done.
"""

from __future__ import annotations

from typing import Any


def score_buyer(buyer: dict[str, Any], invoices: list[dict[str, Any]]) -> dict[str, Any]:
    """Score a buyer 0-100 from their payment history.

    Returns a dict with the score, a confidence level (low/medium/high), a
    breakdown of every term that moved the score, and a trend arrow.
    """
    raise NotImplementedError("step 2: score engine")


def confidence(paid_invoice_count: int) -> str:
    """Map how much history we have onto low / medium / high."""
    raise NotImplementedError("step 2: score engine")
