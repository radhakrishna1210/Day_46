"""Post office -- the one way out of the system.

Non-negotiable #4 is the highest-consequence rule in this project, because it
is the only one whose violation reaches a real stranger. So the redirect to the
owner's own inbox is not "we remember to pass the right address". It is four
independent barriers, any one of which is sufficient:

  1. the recipient is never an argument. _send_email ignores `to` entirely and
     reads TEST_INBOX_EMAIL from .env, so no code path can put a buyer address
     into an SMTP envelope.
  2. a hard check before the socket opens. A resolved recipient that is not
     TEST_INBOX_EMAIL raises RefusedRecipient.
  3. sending is off unless explicitly switched on, so pytest, main.py and the
     simulator never open a socket by accident.
  4. every generated buyer address ends in .example.invalid, which is
     undeliverable by RFC 2606 even if the three above all failed.

WhatsApp and SMS are stubs that log "would send". That is a documented scope
call, not an oversight, and the reason is quoted from config into every audit
entry so a reviewer reads it where it matters.

Quiet hours land here rather than in the brain: a simulated date has no time of
day, and a rule about not messaging someone at 11pm is meaningless without one.
When a real send is about to happen the check uses the real clock, defaulted
inside this module so a caller cannot skip it by forgetting an argument.
"""

from __future__ import annotations

import os
import smtplib
from datetime import date, datetime, time
from email.message import EmailMessage
from typing import Any

from dotenv import load_dotenv

from engine import audit
from engine.config import rules

load_dotenv()

REAL, STUB = "real", "stub"
SENT, WOULD_SEND, BLOCKED, FAILED = "sent", "would_send", "blocked", "failed"

#: Prepended to every real email. If a screenshot of this inbox reaches the
#: video or the repo, it has to be unambiguous that nobody was contacted.
SIMULATION_BANNER = (
    "-- SIMULATED. Sent by the Revenue Recovery Agent demo to its own test "
    "inbox. The addressee named below is synthetic and was never contacted. --"
)


class RefusedRecipient(RuntimeError):
    """Raised when anything but the configured test inbox reaches the envelope."""


def test_inbox() -> str | None:
    """The only address this system is ever allowed to email."""
    value = (os.getenv("TEST_INBOX_EMAIL") or "").strip()
    return value or None


def _channel_mode(channel: str) -> str:
    return str(rules()["channels"].get(channel, STUB))


def in_quiet_hours(now: datetime, config: dict[str, Any] | None = None) -> bool:
    """True inside the window where we do not message anyone."""
    window = (config or rules())["stop_rules"]["quiet_hours"]
    start = time.fromisoformat(str(window["start"]))
    end = time.fromisoformat(str(window["end"]))
    current = now.time()
    if start <= end:                       # a window inside one day
        return start <= current < end
    return current >= start or current < end     # a window across midnight


def _record(delivery: dict[str, Any], invoice_id: str | None, buyer_id: str | None,
            today: date | datetime, log: bool) -> dict[str, Any]:
    if log:
        audit.record(
            invoice_id=invoice_id,
            action=delivery["status"],
            reason=delivery["reason"],
            source="rule",
            today=today,
            buyer_id=buyer_id,
            actor="channels",
            detail={k: v for k, v in delivery.items() if k != "reason"},
        )
    return delivery


def _stub(channel: str, to: str, message: dict[str, Any], rung: int | None) -> dict[str, Any]:
    """Log a would-send. Nothing leaves the process."""
    why = str(rules()["channels"].get("stub_reasons", {}).get(channel, "stubbed"))
    body = str(message.get("body") or "")
    return {
        "channel": channel,
        "to": None,
        "intended_for": to,
        "status": WOULD_SEND,
        "reason": f"{channel} is stubbed: {' '.join(why.split())}",
        "subject": message.get("subject"),
        "body_chars": len(body),
        "rung": rung,
        "message_id": None,
    }


def _send_email(
    to: str,
    message: dict[str, Any],
    *,
    rung: int | None,
    invoice_id: str | None,
    enabled: bool,
    now: datetime | None,
    ignore_quiet_hours: bool,
    simulated_date: date | datetime,
) -> dict[str, Any]:
    """Really send, to the test inbox and nowhere else.

    `to` is recorded as the fiction and then ignored. The recipient comes from
    the environment, is checked, and only then reaches an envelope.
    """
    inbox = test_inbox()
    delivery: dict[str, Any] = {
        "channel": "email",
        "to": inbox,
        "intended_for": to,
        "status": BLOCKED,
        "reason": "",
        "subject": message.get("subject"),
        "body_chars": len(str(message.get("body") or "")),
        "rung": rung,
        "message_id": None,
    }

    if not enabled:
        delivery["reason"] = "sending is off; run with --send-email to deliver"
        return delivery
    if not inbox:
        delivery["reason"] = "TEST_INBOX_EMAIL is not set in .env, so there is nowhere safe to send"
        return delivery

    # Quiet hours, on the real clock, defaulted here so a caller cannot skip it.
    moment = now or datetime.now()
    if in_quiet_hours(moment):
        if not ignore_quiet_hours:
            delivery["reason"] = (
                f"quiet hours: it is {moment.strftime('%H:%M')} and messages are "
                f"not sent between "
                f"{rules()['stop_rules']['quiet_hours']['start']} and "
                f"{rules()['stop_rules']['quiet_hours']['end']}"
            )
            return delivery
        delivery["quiet_hours_overridden"] = True

    config = rules()["channels"]
    host = os.getenv("SMTP_HOST") or config["smtp_host"]
    port = int(os.getenv("SMTP_PORT") or config["smtp_port"])
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    if not user or not password:
        delivery["reason"] = "SMTP_USER or SMTP_PASS is not set in .env"
        return delivery

    envelope = EmailMessage()
    envelope["From"] = user
    envelope["To"] = delivery["to"]
    envelope["Subject"] = str(message.get("subject") or "Invoice reminder")
    envelope["X-Intended-For"] = str(to)
    envelope["X-Invoice"] = str(invoice_id or "")
    envelope["X-Rung"] = str(rung if rung is not None else "")
    envelope["X-Simulated-Date"] = str(simulated_date)
    envelope.set_content(
        f"{SIMULATION_BANNER}\n\n"
        f"Addressed in the simulation to: {to}\n"
        f"{'-' * 70}\n\n"
        f"{message.get('body', '')}\n"
    )

    # Barrier 2, the one that must never be removed. Checked on the envelope
    # itself, at the last possible moment, against the environment variable
    # read directly -- not against test_inbox(), which would only be comparing
    # a value with itself and could never fire. If anything upstream is ever
    # rewired to address a buyer, this is what stops it.
    permitted = (os.getenv("TEST_INBOX_EMAIL") or "").strip()
    if not permitted or envelope["To"] != permitted:
        raise RefusedRecipient(
            f"refusing to email {envelope['To']!r}: the only permitted "
            f"recipient is TEST_INBOX_EMAIL"
        )

    try:
        with smtplib.SMTP(host, port, timeout=int(config.get("smtp_timeout_seconds", 20))) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(envelope)
    except Exception as exc:                    # a dead network must not abort a run
        delivery["status"] = FAILED
        delivery["reason"] = f"{type(exc).__name__}: {exc}"
        return delivery

    delivery["status"] = SENT
    delivery["reason"] = f"delivered to the test inbox, addressed in simulation to {to}"
    delivery["message_id"] = envelope.get("Message-Id")
    return delivery


def send(
    channel: str,
    to: str,
    message: dict[str, Any],
    *,
    invoice_id: str | None = None,
    buyer_id: str | None = None,
    rung: int | None = None,
    today: date | datetime | None = None,
    now: datetime | None = None,
    enabled: bool = False,
    ignore_quiet_hours: bool = False,
    log: bool = True,
) -> dict[str, Any]:
    """Send a message, or log what would have been sent.

    Args:
        channel: email, whatsapp or sms.
        to: who the message is addressed to in the simulation. For email this
            is recorded and then ignored -- see the module docstring.
        message: the writer's output; subject and body are used.
        enabled: real sending. Off unless --send-email was passed.
        now: real wall clock, for quiet hours. Defaults inside the email path.
        ignore_quiet_hours: an explicit human override, recorded as such.

    Returns:
        A delivery record, also written to the audit trail.
    """
    when = today or date.today()
    if _channel_mode(channel) == REAL and channel == "email":
        delivery = _send_email(
            to, message, rung=rung, invoice_id=invoice_id, enabled=enabled,
            now=now, ignore_quiet_hours=ignore_quiet_hours, simulated_date=when,
        )
    else:
        delivery = _stub(channel, to, message, rung)
    return _record(delivery, invoice_id, buyer_id, when, log)


def describe(delivery: dict[str, Any]) -> str:
    """One human-readable line, per the log conventions. No emoji."""
    if delivery["status"] == WOULD_SEND:
        return (f"would send {delivery['channel']} to {delivery['intended_for']} "
                f"-- rung {delivery['rung']}, {delivery['body_chars']} chars "
                f"-- {delivery['reason']}")
    if delivery["status"] == SENT:
        return (f"sent email to {delivery['to']} (addressed in simulation to "
                f"{delivery['intended_for']}) -- rung {delivery['rung']}")
    return (f"{delivery['status']} {delivery['channel']} for "
            f"{delivery['intended_for']} -- {delivery['reason']}")
