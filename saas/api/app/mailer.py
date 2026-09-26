"""Email: queue in the outbox, then deliver over SMTP.

SMTP is configured in saas/api/.env (SMTP_HOST, SMTP_PORT, SMTP_USER,
SMTP_PASS, MAIL_FROM). Gmail with an app password works for development; a
transactional provider's SMTP relay (Amazon SES, Brevo, ZeptoMail, Resend)
for production -- same code, different credentials.

With SMTP_HOST empty (the development default) nothing leaves the machine:
the message is printed to the API log, so a sign-in code can still be read.

Recipients are always the platform's own users. Buyer reminders are NOT sent
by this module -- that stays the owner's decision (CLAUDE.md non-negotiable #4).
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app import settings
from app.models import OutboxEmail

log = logging.getLogger("recova.mail")
MAX_ATTEMPTS = 5

#: Messages "sent" while SMTP is off -- tests read it; the log shows it.
DEV_SENT: list[dict[str, str]] = []


def queue(db: Session, *, to: str, kind: str, subject: str, text: str,
          html_body: str | None = None, tenant_id: str | None = None) -> OutboxEmail:
    row = OutboxEmail(to_email=to, kind=kind, subject=subject, body_text=text,
                      body_html=html_body, tenant_id=tenant_id)
    db.add(row)
    return row


def allowed(to: str) -> bool:
    """RECOVA_EMAIL_ALLOWLIST (comma-separated addresses or @domains): when set,
    only those receive real email -- a safety net for development, where demo
    accounts carry made-up addresses that may belong to real strangers."""
    rules = [r.strip().lower() for r in settings.env("RECOVA_EMAIL_ALLOWLIST").split(",") if r.strip()]
    if not rules:
        return True
    to = to.lower()
    return any(to == r or (r.startswith("@") and to.endswith(r)) for r in rules)


def deliver(db: Session, row: OutboxEmail) -> bool:
    """Try to send one queued email; record the outcome on the row."""
    if settings.smtp_enabled() and not allowed(row.to_email):
        row.status, row.last_error = "blocked", "not sent: outside RECOVA_EMAIL_ALLOWLIST"
        log.warning("email to %s blocked by RECOVA_EMAIL_ALLOWLIST (%s)", row.to_email, row.kind)
        return False
    row.attempts += 1
    try:
        if settings.smtp_enabled():
            _send_smtp(row)
        else:
            DEV_SENT.append({"to": row.to_email, "subject": row.subject, "text": row.body_text})
            log.warning("SMTP not configured -- email NOT sent. To: %s | %s\n%s",
                        row.to_email, row.subject, row.body_text)
            print(f"\n[recova mail -- not sent, SMTP off] To: {row.to_email}\n"
                  f"Subject: {row.subject}\n{row.body_text}\n", flush=True)
        row.status, row.sent_at, row.last_error = "sent", datetime.now(timezone.utc), None
        return True
    except Exception as exc:  # noqa: BLE001 -- any SMTP failure is recorded, never raised
        row.last_error = f"{type(exc).__name__}: {exc}"[:500]
        row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "queued"
        log.error("email to %s failed (attempt %s): %s", row.to_email, row.attempts, row.last_error)
        return False


def deliver_pending(db: Session, limit: int = 50) -> int:
    """Send everything still queued. Called after each request that queued
    mail, and safe to call from a scheduler."""
    rows = db.query(OutboxEmail).filter(OutboxEmail.status == "queued").limit(limit).all()
    sent = sum(1 for row in rows if deliver(db, row))
    db.commit()
    return sent


def _send_smtp(row: OutboxEmail) -> None:
    msg = EmailMessage()
    msg["From"] = settings.env("MAIL_FROM") or settings.env("SMTP_USER")
    msg["To"] = row.to_email
    msg["Subject"] = row.subject
    msg.set_content(row.body_text)
    if row.body_html:
        msg.add_alternative(row.body_html, subtype="html")
    host, port = settings.env("SMTP_HOST"), int(settings.env("SMTP_PORT", "587") or 587)
    user, password = settings.env("SMTP_USER"), settings.env("SMTP_PASS")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------

def _html(title: str, intro: str, big: str | None = None, outro: str = "",
          button: tuple[str, str] | None = None, body_html: str = "") -> str:
    code = (f'<p style="font:600 32px/1 ui-monospace,Menlo,monospace;letter-spacing:8px;'
            f'margin:24px 0;color:#0e1116">{html.escape(big)}</p>') if big else ""
    if button:
        label, href = button
        code += (f'<p style="margin:24px 0"><a href="{html.escape(href, quote=True)}" '
                 'style="background:#0f766e;color:#fff;text-decoration:none;padding:12px 20px;'
                 f'border-radius:10px;font-weight:600;display:inline-block">{html.escape(label)}</a></p>')
    code += body_html
    return (
        '<div style="background:#f6f5f1;padding:32px 16px;font-family:system-ui,Segoe UI,sans-serif">'
        '<div style="max-width:480px;margin:auto;background:#fff;border:1px solid #e4e1d8;'
        'border-radius:16px;padding:32px">'
        '<div style="font-weight:600;font-size:17px;color:#0f766e">Recova</div>'
        f'<h1 style="font-size:22px;margin:16px 0 8px;color:#0e1116">{html.escape(title)}</h1>'
        f'<p style="color:#454a52;font-size:15px;line-height:1.5">{html.escape(intro)}</p>{code}'
        f'<p style="color:#7a7f87;font-size:13px;line-height:1.5">{html.escape(outro)}</p>'
        '</div></div>')


CODE_COPY = {
    "login": ("Your Recova sign-in code", "Use this code to sign in to Recova."),
    "verify": ("Confirm your email", "Use this code to confirm your email address for Recova."),
    "reset": ("Reset your Recova password", "Use this code to set a new password."),
}


def code_email(purpose: str, code: str, minutes: int) -> tuple[str, str, str]:
    subject, intro = CODE_COPY[purpose]
    outro = (f"The code expires in {minutes} minutes and works once. If you did not ask "
             f"for it, you can ignore this email.")
    text = f"{intro}\n\n    {code}\n\n{outro}\n"
    return f"{code} is your code — {subject}", text, _html(subject, intro, code, outro)


ROLE_WORDS = {
    "admin": "an admin (manage the team and business settings)",
    "member": "a member (work invoices, payments and reminders)",
    "viewer": "a viewer (see everything, change nothing)",
}


def invite_email(business: str, inviter: str, role: str, link: str,
                 days: int) -> tuple[str, str, str]:
    subject = f"{inviter} invited you to {business} on Recova"
    intro = (f"{inviter} invited you to join {business} on Recova as {ROLE_WORDS[role]}. "
             f"Recova tracks who owes {business} money and what to do about it today.")
    outro = (f"The link works for {days} days, for this email address only. If you were not "
             f"expecting it, you can ignore this email.")
    text = f"{intro}\n\nAccept the invitation:\n{link}\n\n{outro}\n"
    return subject, text, _html(f"Join {business}", intro, None, outro,
                                button=("Accept invitation", link))
