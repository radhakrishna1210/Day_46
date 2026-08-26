"""Tests for the CALLER side of early warnings: what sim/run_sim.py decides
is worth writing to the audit trail, and what report/build_report.py then
shows.

engine/watchdog.py's early_warnings() computes a real risk_band (low/watch/
high) for every invoice in the window -- see tests/test_watchdog.py. That is
one layer down from the question these tests answer: does "low" ever leak
into the audit trail or the report. A seed-42 run happening not to produce a
visible difference is not proof of that -- these tests construct a low-band
invoice directly, so the guarantee holds regardless of what any one seed's
generated data looks like.
"""

from __future__ import annotations

from datetime import date

import pytest

from engine import audit
from report import build_report
from sim import run_sim

TODAY = date(2026, 8, 24)


@pytest.fixture(autouse=True)
def _quiet_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    audit.enable()
    yield


def invoice(*, invoice_id: str, buyer_id: str, acceptance: str = "2026-07-19",
           agreed_days: int = 45, status: str = "open", amount: int = 50_000_000) -> dict:
    """Due in 9 days from TODAY on 45-day terms -- see tests/test_watchdog.py
    for the same arithmetic, reused here so both files agree on one fixture."""
    return {
        "invoice_id": invoice_id, "buyer_id": buyer_id,
        "acceptance_date": acceptance, "written_agreement": True,
        "agreed_days": agreed_days, "agreed_due_date": None,
        "status": status, "amount_paise": amount, "amount_paid_paise": 0,
    }


def _watch_band_trio(buyer_id: str = "BUY-WATCH") -> list[dict]:
    """One not-yet-due invoice plus two long-overdue ones for the same
    buyer -- poor score (passed separately) + this buyer's prior-overdue
    pattern is 2 of 3 categories, landing the not-yet-due invoice on "watch"
    (see tests/test_watchdog.py test_risk_band_covers_low_watch_and_high,
    which exercises the identical shape directly against watchdog.py)."""
    return [
        invoice(invoice_id=f"INV-{buyer_id}", buyer_id=buyer_id),
        invoice(invoice_id=f"INV-{buyer_id}-P1", buyer_id=buyer_id, acceptance="2026-01-01"),
        invoice(invoice_id=f"INV-{buyer_id}-P2", buyer_id=buyer_id, acceptance="2026-02-01"),
    ]


def test_a_low_band_invoice_never_gets_an_audit_entry_or_a_report_row() -> None:
    """Constructed directly, not inferred from seed-42's own data: a
    good-signal buyer's not-yet-due invoice lands in the "low" band, and
    that low band must never reach the audit trail or the report."""
    low = invoice(invoice_id="INV-LOW", buyer_id="BUY-LOW")
    scores = {"BUY-LOW": {"score": 90, "confidence": "high"}}

    assert run_sim._notable_early_warnings([low], [], scores, TODAY) == []

    warned: set[str] = set()
    run_sim._raise_early_warnings([low], [], scores, TODAY, warned)

    assert "INV-LOW" not in warned
    assert audit.entries() == []                       # nothing logged at all
    assert build_report._early_warning_rows() == []     # nothing for the report to show


def test_a_watch_band_invoice_does_get_logged_and_shown() -> None:
    """The contrast case -- proves the low-band test above is actually
    exercising the filter against a real signal, not just an empty world
    with nothing to log in the first place."""
    scores = {"BUY-WATCH": {"score": 10, "confidence": "medium"}}
    warned: set[str] = set()
    run_sim._raise_early_warnings(_watch_band_trio(), [], scores, TODAY, warned)

    assert "INV-BUY-WATCH" in warned
    logged = [e for e in audit.entries() if e["action"] == "early_warning_raised"]
    assert [e["invoice_id"] for e in logged] == ["INV-BUY-WATCH"]
    assert logged[0]["detail"]["risk_band"] == "watch"

    rows = build_report._early_warning_rows()
    assert [r["invoice_id"] for r in rows] == ["INV-BUY-WATCH"]
    assert rows[0]["risk_band"] == "watch"


def test_a_low_band_invoice_stays_hidden_even_alongside_a_watch_band_one() -> None:
    """The exact scenario the review asked for: a low-band invoice sitting
    in the SAME batch as a notable one must not leak into either the audit
    trail or the report, while its watch-band neighbour does appear."""
    low = invoice(invoice_id="INV-LOW", buyer_id="BUY-LOW")
    scores = {
        "BUY-LOW": {"score": 90, "confidence": "high"},
        "BUY-WATCH": {"score": 10, "confidence": "medium"},
    }
    invoices = [low, *_watch_band_trio()]

    warned: set[str] = set()
    run_sim._raise_early_warnings(invoices, [], scores, TODAY, warned)

    logged_ids = {e["invoice_id"] for e in audit.entries() if e["action"] == "early_warning_raised"}
    assert logged_ids == {"INV-BUY-WATCH"}
    assert "INV-LOW" not in logged_ids

    row_ids = {r["invoice_id"] for r in build_report._early_warning_rows()}
    assert row_ids == {"INV-BUY-WATCH"}
    assert "INV-LOW" not in row_ids
