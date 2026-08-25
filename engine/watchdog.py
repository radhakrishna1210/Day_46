"""Watchdog -- finds the invoices that have gone overdue. Pure rules, date math.

Runs once per simulated day. The clock is always passed in, never read from
date.today(), so tests and the simulator can time-travel.

Lateness is measured against the STATUTORY due date from engine.law, not the
date the contract claimed. On an invoice whose terms said 90 days, that is the
difference between chasing on day 91 and chasing on day 46.

    python engine/watchdog.py --as-of 2026-08-24
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Allow running this file directly as a script as well as importing it, by
# putting the repo root on the path when there is no enclosing package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import validate
from engine.law import _as_date, days_gained_by_law, statutory_due_date

#: An invoice in any of these states still has money owing on it.
UNSETTLED_STATUSES = frozenset({"open", "partially_paid", "disputed"})


def is_unsettled(invoice: dict[str, Any]) -> bool:
    """True while money is still owed on this invoice."""
    return invoice.get("status") in UNSETTLED_STATUSES


def outstanding_paise(invoice: dict[str, Any]) -> int:
    """What is still owed, after any partial payments."""
    return int(invoice["amount_paise"]) - int(invoice.get("amount_paid_paise", 0))


def days_overdue(invoice: dict[str, Any], today: date) -> int:
    """Days past the statutory due date. Negative means not yet due."""
    return (today - statutory_due_date(invoice)).days


def is_overdue(invoice: dict[str, Any], today: date) -> bool:
    """True when this invoice is unsettled and its statutory deadline has passed.

    A malformed invoice (see engine/validate.py) is never "overdue" -- its due
    date cannot be trusted, so it must not enter the work queue at all. It is
    not being called settled here; it is being excluded from a question that
    cannot be honestly answered about it. Callers that need the reason why
    should go through engine.validate directly.
    """
    if validate.invalid_reason(invoice, today) is not None:
        return False
    return is_unsettled(invoice) and days_overdue(invoice, today) > 0


def overdue_invoices(invoices: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """The work queue for today: unsettled invoices past their statutory due date.

    A malformed invoice (engine.validate.invalid_reason) never appears here --
    see is_overdue() -- so nothing malformed ever reaches engine/law.py or
    engine/brain.py by way of this queue. It still exists in the caller's full
    invoice list, so it is not lost; engine.validate.audit_invalid() is what a
    caller uses to record and surface why it was excluded.

    A duplicate invoice_id (TC-052) is excluded here too, alongside is_overdue's
    per-invoice checks -- is_overdue() cannot see it, since a duplicate is a
    property of the whole batch, not of one invoice.

    Sorted by money at risk, largest first, then by how long it has been
    outstanding -- so if a run is ever cut short, the expensive cases were
    handled first.
    """
    duplicates = validate.duplicate_reasons(invoices)
    queue = [inv for inv in invoices
             if inv["invoice_id"] not in duplicates and is_overdue(inv, today)]
    queue.sort(key=lambda inv: (-outstanding_paise(inv), -days_overdue(inv, today)))
    return queue


def work_item(invoice: dict[str, Any], today: date) -> dict[str, Any]:
    """One queue entry, with the dates spelled out for the audit trail."""
    return {
        "invoice_id": invoice["invoice_id"],
        "buyer_id": invoice["buyer_id"],
        "status": invoice["status"],
        "outstanding_paise": outstanding_paise(invoice),
        "statutory_due_date": statutory_due_date(invoice).isoformat(),
        "agreed_due_date": invoice.get("agreed_due_date"),
        "days_overdue": days_overdue(invoice, today),
        "days_gained_by_law": days_gained_by_law(invoice),
    }


# --- promises -------------------------------------------------------------
# A promise is what engine/promises.py will record on Day 7:
#   {"invoice_id": ..., "promised_date": "2026-09-05", "status": "open"}

def is_promise_broken(promise: dict[str, Any], today: date) -> bool:
    """True when a promised date has passed and the promise is still open."""
    if promise.get("status") != "open":
        return False
    return _as_date(promise["promised_date"]) < today


def due_promises(promises: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Promises whose date has passed without the money arriving."""
    return [p for p in promises if is_promise_broken(p, today)]


def main() -> int:
    from data import store

    from engine.money import enable_unicode_output, format_inr

    enable_unicode_output()
    parser = argparse.ArgumentParser(description="Show today's overdue work queue.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None,
                        help="simulation date (default: the dataset's simulation_start)")
    parser.add_argument("--limit", type=int, default=15, help="rows to print")
    args = parser.parse_args()

    if not store.dataset_exists():
        print(f"no dataset found -- {store.REGENERATE_HINT}")
        return 1

    invoices = store.load_invoices()
    today = args.as_of or _as_date(store.load_meta()["simulation_start"])
    queue = overdue_invoices(invoices, today)
    unsettled = [inv for inv in invoices if is_unsettled(inv)]
    at_risk = sum(outstanding_paise(inv) for inv in queue)

    print(f"watchdog run for {today.isoformat()}")
    print(f"  unsettled invoices         {len(unsettled):>6}")
    print(f"  overdue (the work queue)   {len(queue):>6}")
    print(f"  not yet due                {len(unsettled) - len(queue):>6}")
    print(f"  money at risk              {format_inr(at_risk, 'Rs '):>16}")
    print()
    print(f"  {'invoice':<16}{'buyer':<9}{'outstanding':>14}{'overdue':>9}{'law gains':>11}  status")
    for invoice in queue[:args.limit]:
        item = work_item(invoice, today)
        gained = f"{item['days_gained_by_law']}d" if item["days_gained_by_law"] else "-"
        print(
            f"  {item['invoice_id']:<16}{item['buyer_id']:<9}"
            f"{format_inr(item['outstanding_paise'], 'Rs '):>14}"
            f"{item['days_overdue']:>8}d{gained:>11}  {item['status']}"
        )
    if len(queue) > args.limit:
        print(f"  ... and {len(queue) - args.limit} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
