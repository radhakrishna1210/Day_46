"""The overview screen: money owed, how late, who, and what is coming due."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends

from app import bridge
from app.clock import today_for
from app.deps import TenantContext, tenant_context
from app.models import Buyer, Invoice

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

#: Days past the STATUTORY due date (MSMED Act s.15), not the contract's.
AGING_BUCKETS = ((1, 15, "1-15 days"), (16, 30, "16-30 days"), (31, 45, "31-45 days"),
                 (46, 90, "46-90 days"), (91, 10**6, "90+ days"))


@router.get("")
def overview(ctx: TenantContext = Depends(tenant_context), as_of: date | None = None) -> dict:
    today = today_for(as_of)
    buyers = ctx.db.scalars(ctx.scoped(Buyer)).all()
    invoices = ctx.db.scalars(ctx.scoped(Invoice)).all()
    scores = bridge.score_buyers(buyers, invoices, today)

    open_rows = []
    for inv in invoices:
        owed = bridge.outstanding(inv, today)
        if owed <= 0:
            continue
        legal = bridge.legal_summary(inv, today)
        open_rows.append((inv, owed, legal))

    overdue = [(i, o, l) for i, o, l in open_rows if l["days_overdue"] > 0]
    aging = []
    for low, high, label in AGING_BUCKETS:
        rows = [(i, o) for i, o, l in overdue if low <= l["days_overdue"] <= high]
        aging.append({"bucket": label, "count": len(rows), "paise": sum(o for _, o in rows)})

    by_buyer: dict[str, dict] = {}
    for inv, owed, legal in overdue:
        entry = by_buyer.setdefault(inv.buyer_id, {
            "buyer_id": inv.buyer_id, "name": inv.buyer.name, "overdue_paise": 0,
            "invoices": 0, "oldest_days": 0, "interest_paise": 0,
            "score": scores[inv.buyer_id]["score"],
            "confidence": scores[inv.buyer_id]["confidence"]})
        entry["overdue_paise"] += owed
        entry["invoices"] += 1
        entry["interest_paise"] += legal["interest_paise"]
        entry["oldest_days"] = max(entry["oldest_days"], legal["days_overdue"])
    top_buyers = sorted(by_buyer.values(), key=lambda e: -e["overdue_paise"])[:6]

    # Money in, week by week, for the last 12 weeks.
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=11)
    weeks = [{"week_of": (start + timedelta(weeks=n)).isoformat(), "paise": 0} for n in range(12)]
    for inv in invoices:
        for p in inv.payments:
            if start <= p.paid_on <= today:
                weeks[(p.paid_on - start).days // 7]["paise"] += p.amount_paise

    names = {b.id: b.name for b in buyers}
    due_soon = [(i, o, l) for i, o, l in open_rows
                if 0 <= l["days_to_due"] <= 14 and not i.disputed]

    return {
        "as_of": today.isoformat(),
        "totals": {
            "receivable_paise": sum(o for _, o, _ in open_rows),
            "overdue_paise": sum(o for _, o, _ in overdue),
            "overdue_invoices": len(overdue),
            "open_invoices": len(open_rows),
            "interest_accrued_paise": sum(l["interest_paise"] for _, _, l in overdue),
            "disputed_paise": sum(o for i, o, _ in open_rows if i.disputed),
            "buyers": len(buyers),
            "avg_days_overdue": (round(sum(l["days_overdue"] for _, _, l in overdue)
                                       / len(overdue), 1) if overdue else 0),
            "collected_12w_paise": sum(w["paise"] for w in weeks),
        },
        "aging": aging,
        "top_buyers": top_buyers,
        "collections": weeks,
        "due_soon": [{"id": i.id, "invoice_number": i.invoice_number, "buyer": i.buyer.name,
                      "outstanding_paise": o, "due_date": l["statutory_due_date"],
                      "days_to_due": l["days_to_due"]}
                     for i, o, l in sorted(due_soon, key=lambda r: r[2]["days_to_due"])][:8],
        "early_warnings": [
            {"invoice_number": w["invoice_id"], "buyer": names.get(w["buyer_id"], ""),
             "outstanding_paise": w["outstanding_paise"], "days_until_due": w["days_until_due"],
             "risk_band": w["risk_band"], "reasons": w["reasons"]}
            for w in bridge.early_warnings(buyers, invoices, scores, today)[:8]],
    }
