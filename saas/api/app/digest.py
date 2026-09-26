"""The daily run and the morning digest email.

Once a day, per business: compute today's decision queue (the same code the
Decisions page runs), write one audit row saying what the rules found, and
email a short digest to the team -- owners, admins and members with a verified
email who have not switched it off. Viewers and suspended people get nothing;
suspended businesses are skipped. Buyers are NEVER emailed from here.

The loop (start_scheduler) only decides WHEN; run_daily() is a plain function,
so tests and the "run now" buttons call it directly.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, mailer, settings
from app.clock import today_for
from app.models import WRITERS, Invoice, Membership, Promise, Tenant, User
from app.routers.decisions import build_queue
from engine.money import format_inr

log = logging.getLogger("recova.daily")

ACT_KINDS = ("send", "payment_plan", "counter_settle")
KIND_WORDS = {"send": "Send a reminder", "payment_plan": "Offer a payment plan",
              "counter_settle": "Settlement offer", "handoff": "Needs a person"}
TOP_N = 6


# --------------------------------------------------------------------------
# what goes in a digest
# --------------------------------------------------------------------------

def summarize(db: Session, tenant: Tenant, today: date) -> dict[str, Any]:
    q = build_queue(db, tenant, today)
    rows = q["decisions"]
    act = [r for r in rows if r["kind"] in ACT_KINDS]
    handoff = [r for r in rows if r["kind"] == "handoff"]
    promises = db.scalars(select(Promise).join(Invoice).where(
        Invoice.tenant_id == tenant.id, Promise.status == "open",
        Promise.promised_date <= today)).all()
    due_today = [p for p in promises if p.promised_date == today]
    lapsed = [p for p in promises if p.promised_date < today]
    return {
        "today": today.isoformat(),
        "tally": q["tally"],
        "to_act": len(act), "handoffs": len(handoff),
        "overdue_invoices": len(rows),
        "overdue_paise": sum(r["outstanding_paise"] for r in rows),
        "interest_paise": sum(r["interest_paise"] for r in rows),
        "top": [{"kind": r["kind"], "buyer": r["buyer"]["name"],
                 "invoice_number": r["invoice_number"], "invoice_id": r["invoice_id"],
                 "outstanding_paise": r["outstanding_paise"], "days_overdue": r["days_overdue"],
                 "reason": r["reason"]} for r in (act + handoff)[:TOP_N]],
        "promises_due_today": [{"buyer": p.invoice.buyer.name,
                                "invoice_number": p.invoice.invoice_number} for p in due_today],
        "promises_lapsed": len(lapsed),
    }


def worth_sending(s: dict[str, Any]) -> bool:
    """No news is no email: skip a day with nothing to do."""
    return bool(s["to_act"] or s["handoffs"] or s["promises_due_today"] or s["promises_lapsed"])


def recipients(db: Session, tenant: Tenant) -> list[User]:
    members = db.scalars(select(Membership).where(Membership.tenant_id == tenant.id,
                                                  Membership.role.in_(WRITERS))).all()
    return [m.user for m in members
            if m.user.email_verified and not m.user.digest_opt_out and m.user.suspended_at is None]


def compose(tenant: Tenant, s: dict[str, Any]) -> tuple[str, str, str]:
    day = datetime.fromisoformat(s["today"]).strftime("%a %d %b")
    headline = []
    if s["to_act"]:
        headline.append(f"{s['to_act']} to act on")
    if s["handoffs"]:
        headline.append(f"{s['handoffs']} need a person")
    if s["promises_due_today"]:
        headline.append(f"{len(s['promises_due_today'])} promise(s) due today")
    subject = f"{tenant.legal_name} · {day}: " + (", ".join(headline) or "promises to check")
    link = f"{settings.public_url()}/app/decisions"

    lines = [f"Today for {tenant.legal_name} ({day}):", ""]
    lines.append(f"Overdue: {s['overdue_invoices']} invoices, {format_inr(s['overdue_paise'])} "
                 f"outstanding, {format_inr(s['interest_paise'])} statutory interest so far.")
    if s["top"]:
        lines += ["", "What the rules say to do:"]
        lines += [f"  - {KIND_WORDS.get(t['kind'], t['kind'])}: {t['buyer']} "
                  f"{t['invoice_number']} ({format_inr(t['outstanding_paise'])}, "
                  f"{t['days_overdue']} days overdue)" for t in s["top"]]
        more = s["to_act"] + s["handoffs"] - len(s["top"])
        if more > 0:
            lines.append(f"  ...and {more} more.")
    if s["promises_due_today"]:
        lines += ["", "Promised to pay today:"]
        lines += [f"  - {p['buyer']} {p['invoice_number']}" for p in s["promises_due_today"]]
    if s["promises_lapsed"]:
        lines += ["", f"{s['promises_lapsed']} earlier promise(s) have passed without being "
                      "marked kept or broken."]
    lines += ["", f"Open today's decisions: {link}", "",
              "Recova drafts every message; nothing is sent to your buyers until you send it. "
              "Turn this email off in Settings."]
    text = "\n".join(lines) + "\n"

    items = "".join(
        f'<tr><td style="padding:8px 0;border-top:1px solid #eee;color:#0e1116">'
        f'<b>{html.escape(t["buyer"])}</b> <span style="color:#7a7f87">{html.escape(t["invoice_number"])}</span><br>'
        f'<span style="color:#454a52;font-size:13px">{html.escape(KIND_WORDS.get(t["kind"], t["kind"]))} · '
        f'{html.escape(format_inr(t["outstanding_paise"]))} · {t["days_overdue"]} days overdue</span></td></tr>'
        for t in s["top"])
    table = f'<table style="width:100%;border-collapse:collapse;margin:8px 0">{items}</table>' if items else ""
    intro = (f"{s['overdue_invoices']} overdue invoices, {format_inr(s['overdue_paise'])} outstanding, "
             f"{format_inr(s['interest_paise'])} statutory interest so far.")
    outro = ("Recova drafts every message; nothing is sent to your buyers until you send it. "
             "Turn this email off in Settings.")
    return subject, text, mailer._html(f"Today for {tenant.legal_name}", intro, None, outro,
                                       button=("Open today's decisions", link), body_html=table)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run_for_tenant(db: Session, tenant: Tenant, today: date, *, only: User | None = None
                   ) -> dict[str, Any]:
    """Compute, audit and (if there is anything to say) queue the digest. With
    `only`, email just that person -- the "send me today's digest" button --
    and do not mark the business's daily run as done."""
    s = summarize(db, tenant, today)
    people = [only] if only else recipients(db, tenant)
    sent_to: list[str] = []
    if people and (only or worth_sending(s)):
        subject, text, html_body = compose(tenant, s)
        for user in people:
            mailer.queue(db, to=user.email, kind="daily_digest", subject=subject, text=text,
                         html_body=html_body, tenant_id=tenant.id)
            sent_to.append(user.email)
    if only is None:
        tenant.last_daily_run = today
        audit.record(db, tenant_id=tenant.id, actor="agent", source="rule", action="daily_run",
                     reason=(f"Daily run for {today.isoformat()}: {s['to_act']} to act on, "
                             f"{s['handoffs']} for a person, {len(s['promises_due_today'])} "
                             f"promise(s) due today; digest emailed to "
                             f"{len(sent_to)} {'person' if len(sent_to) == 1 else 'people'}"),
                     detail={"tally": s["tally"], "emailed": len(sent_to)})
    return {"tenant": tenant.legal_name, "summary": s, "emailed": sent_to}


def run_daily(db: Session, today: date | None = None, *, force: bool = False) -> list[dict]:
    """Every active business that has not had today's run yet (or all, with force)."""
    today = today or today_for()
    done = []
    for tenant in db.scalars(select(Tenant).where(Tenant.suspended_at.is_(None))).all():
        if not force and tenant.last_daily_run == today:
            continue
        try:
            done.append(run_for_tenant(db, tenant, today))
            db.commit()
        except Exception:  # noqa: BLE001 -- one broken business must not stop the rest
            db.rollback()
            log.exception("daily run failed for tenant %s", tenant.id)
    mailer.deliver_pending(db)
    return done


# --------------------------------------------------------------------------
# the clock
# --------------------------------------------------------------------------

def scheduler_enabled() -> bool:
    return settings.env("RECOVA_SCHEDULER", "on").lower() not in ("off", "0", "false", "no")


def digest_hour() -> int:
    """Local server hour (0-23) after which the day's run happens."""
    return int(settings.env("RECOVA_DIGEST_HOUR", "8") or 8)


async def _loop(session_factory, every_seconds: int) -> None:
    while True:
        try:
            if datetime.now().hour >= digest_hour():
                await asyncio.to_thread(_tick, session_factory)
            else:
                await asyncio.to_thread(_retry_mail, session_factory)
        except Exception:  # noqa: BLE001 -- keep the loop alive
            log.exception("scheduler tick failed")
        await asyncio.sleep(every_seconds)


def _tick(session_factory) -> None:
    with session_factory() as db:
        ran = run_daily(db)
        if ran:
            log.warning("daily run: %s business(es)", len(ran))


def _retry_mail(session_factory) -> None:
    with session_factory() as db:
        mailer.deliver_pending(db)


def start_scheduler(session_factory) -> asyncio.Task | None:
    if not scheduler_enabled():
        return None
    every = int(settings.env("RECOVA_SCHEDULER_SECONDS", "900") or 900)
    return asyncio.create_task(_loop(session_factory, every))
