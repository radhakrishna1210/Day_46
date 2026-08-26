"""Tests for the post office.

Non-negotiable #4 is the only rule in this project whose violation reaches a
real stranger, so most of this file is about proving that cannot happen. The
strongest tests here are the ones that make smtplib itself explode: if a socket
is never opened, nothing can be delivered to anyone.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from engine import audit, channels

TODAY = date(2026, 8, 24)
INBOX = "owner@example.invalid"
BUYER_ADDRESS = "farid.shaikh@meridian-logistics.example.invalid"

MESSAGE = {"subject": "Invoice INV-2026-0016 - 146 days overdue",
           "body": "Dear Farid Shaikh,\n\nThis is a reminder about the outstanding "
                   "invoice.\n\nRegards,\nA. Placeholder"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "LOG_PATH", tmp_path / "audit_log.jsonl")
    monkeypatch.setenv("TEST_INBOX_EMAIL", INBOX)
    monkeypatch.setenv("SMTP_USER", "demo@example.invalid")
    monkeypatch.setenv("SMTP_PASS", "app-password")
    audit.enable()
    yield


class Explode:
    """An SMTP that fails the test if it is ever constructed."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("a socket was opened when none should have been")


class Capture:
    """A fake SMTP that records the envelope instead of sending it."""

    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        self.user = user

    def send_message(self, envelope):
        Capture.sent.append(envelope)


@pytest.fixture
def captured(monkeypatch):
    Capture.sent = []
    monkeypatch.setattr(channels.smtplib, "SMTP", Capture)
    return Capture.sent


def email(**kwargs):
    defaults = dict(invoice_id="INV-2026-0016", buyer_id="BUY-07", rung=2,
                    today=TODAY, now=datetime(2026, 8, 24, 11, 0))
    defaults.update(kwargs)
    return channels.send("email", BUYER_ADDRESS, MESSAGE, **defaults)


# --- nothing is sent unless it is asked for ------------------------------

def test_sending_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """pytest, main.py and the simulator must never open a socket by accident."""
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    delivery = email()
    assert delivery["status"] == channels.BLOCKED
    assert "sending is off" in delivery["reason"]


def test_a_stub_channel_never_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    for channel in ("whatsapp", "sms"):
        delivery = channels.send(channel, "+91-90000-00007", MESSAGE,
                                 invoice_id="INV-1", buyer_id="BUY-07",
                                 rung=2, today=TODAY, enabled=True)
        assert delivery["status"] == channels.WOULD_SEND
        assert delivery["to"] is None


def test_the_stub_records_why_it_is_a_stub() -> None:
    delivery = channels.send("whatsapp", "+91-90000-00007", MESSAGE,
                             invoice_id="INV-1", today=TODAY, enabled=True)
    assert "business verification" in delivery["reason"]


def test_the_stub_line_reads_as_one_log_line() -> None:
    delivery = channels.send("whatsapp", "+91-90000-00007", MESSAGE,
                             invoice_id="INV-1", rung=2, today=TODAY)
    line = channels.describe(delivery)
    assert line.startswith("would send whatsapp to +91-90000-00007")
    assert "\n" not in line


# --- the recipient can only ever be the test inbox -----------------------

def test_the_buyer_address_never_reaches_the_envelope(captured) -> None:
    """The headline safety property, asserted on the actual envelope."""
    delivery = email(enabled=True)
    assert delivery["status"] == channels.SENT
    assert len(captured) == 1
    envelope = captured[0]
    assert envelope["To"] == INBOX
    assert BUYER_ADDRESS not in str(envelope["To"])


def test_the_intended_recipient_is_recorded_not_used(captured) -> None:
    """A reviewer must be able to see both where it went and who it was for."""
    delivery = email(enabled=True)
    assert delivery["to"] == INBOX
    assert delivery["intended_for"] == BUYER_ADDRESS
    assert captured[0]["X-Intended-For"] == BUYER_ADDRESS


def test_email_refuses_any_address_that_is_not_the_test_inbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the redirect is ever rewired, this is the barrier that must fire."""
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    monkeypatch.setattr(channels, "test_inbox", lambda: "somebody-else@real.example")

    with pytest.raises(channels.RefusedRecipient):
        channels._send_email(
            BUYER_ADDRESS, MESSAGE, rung=2, invoice_id="INV-1", enabled=True,
            now=datetime(2026, 8, 24, 11, 0), ignore_quiet_hours=False,
            simulated_date=TODAY,
        )


def test_nothing_is_sent_when_no_test_inbox_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_INBOX_EMAIL", raising=False)
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    delivery = email(enabled=True)
    assert delivery["status"] == channels.BLOCKED
    assert "nowhere safe" in delivery["reason"]


def test_nothing_is_sent_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_PASS", raising=False)
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    delivery = email(enabled=True)
    assert delivery["status"] == channels.BLOCKED


# --- the simulation banner ------------------------------------------------

def test_every_real_email_carries_the_simulation_banner(captured) -> None:
    """A screenshot of this inbox must not read as a real dunning letter."""
    email(enabled=True)
    body = captured[0].get_content()
    assert channels.SIMULATION_BANNER in body
    assert "never contacted" in body
    assert BUYER_ADDRESS in body          # named as the fiction, in the body


def test_the_headers_carry_the_provenance(captured) -> None:
    email(enabled=True)
    envelope = captured[0]
    assert envelope["X-Invoice"] == "INV-2026-0016"
    assert envelope["X-Rung"] == "2"
    assert envelope["X-Simulated-Date"] == str(TODAY)


# --- quiet hours ----------------------------------------------------------

@pytest.mark.parametrize(("hour", "quiet"), [
    (9, False), (11, False), (20, False),
    (21, True), (23, True), (2, True), (8, True),
])
def test_the_quiet_window_crosses_midnight(hour: int, quiet: bool) -> None:
    assert channels.in_quiet_hours(datetime(2026, 8, 24, hour, 30)) is quiet


def test_a_send_inside_quiet_hours_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    delivery = email(enabled=True, now=datetime(2026, 8, 24, 22, 30))
    assert delivery["status"] == channels.BLOCKED
    assert "quiet hours" in delivery["reason"]


def test_quiet_hours_cannot_be_skipped_by_omitting_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check defaults inside the send path, not in the caller.

    A caller that forgets to pass `now` must not thereby bypass the rule, so
    the default is datetime.now() at the point of sending.
    """
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 24, 23, 15)

    monkeypatch.setattr(channels, "datetime", FixedDatetime)
    delivery = channels.send("email", BUYER_ADDRESS, MESSAGE, invoice_id="INV-1",
                             rung=2, today=TODAY, enabled=True)      # no `now`
    assert delivery["status"] == channels.BLOCKED
    assert "quiet hours" in delivery["reason"]


def test_the_override_is_recorded_rather_than_silent(captured) -> None:
    delivery = email(enabled=True, now=datetime(2026, 8, 24, 22, 30),
                     ignore_quiet_hours=True)
    assert delivery["status"] == channels.SENT
    assert delivery["quiet_hours_overridden"] is True


# --- failures are a status, not a crash ----------------------------------

def test_an_smtp_failure_does_not_abort_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def __init__(self, *a, **k):
            raise OSError("network is down")

    monkeypatch.setattr(channels.smtplib, "SMTP", Broken)
    delivery = email(enabled=True)
    assert delivery["status"] == channels.FAILED
    assert "network is down" in delivery["reason"]


# --- everything is audited -----------------------------------------------

def test_every_delivery_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    email()
    channels.send("whatsapp", "+91-90000-00007", MESSAGE, invoice_id="INV-2026-0016",
                  buyer_id="BUY-07", rung=2, today=TODAY)
    entries = audit.entries()
    assert [e["action"] for e in entries] == ["blocked", "would_send"]
    assert all(e["actor"] == "channels" for e in entries)


def test_the_audit_entry_names_both_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    email()
    detail = audit.entries()[0]["detail"]
    assert detail["intended_for"] == BUYER_ADDRESS
    assert detail["channel"] == "email"


# --- a re-run must not duplicate an already-delivered email ---------------

def test_a_second_send_the_same_day_is_skipped_not_resent(captured) -> None:
    """Re-running after an interrupt must not duplicate real emails already sent."""
    first = email(enabled=True)
    assert first["status"] == channels.SENT
    assert len(captured) == 1

    second = email(enabled=True)
    assert second["status"] == channels.SKIPPED
    assert "already sent" in second["reason"]
    assert len(captured) == 1        # no second envelope was ever opened


def test_the_skip_is_itself_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    """The skip is a decision, not a silent no-op -- non-negotiable #1."""
    monkeypatch.setattr(channels.smtplib, "SMTP", Capture)
    email(enabled=True)
    email(enabled=True)
    actions = [e["action"] for e in audit.entries()]
    assert actions == [channels.SENT, channels.SKIPPED]


def test_a_different_day_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is per simulated day, not a blanket 'ever sent'."""
    monkeypatch.setattr(channels.smtplib, "SMTP", Capture)
    email(enabled=True, today=date(2026, 8, 24))
    second = email(enabled=True, today=date(2026, 8, 25),
                    now=datetime(2026, 8, 25, 11, 0))
    assert second["status"] == channels.SENT


def test_the_stub_channel_still_logs_every_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    channels.send("whatsapp", "+91-90000-00007", MESSAGE, invoice_id="INV-1",
                  rung=2, today=TODAY, enabled=True)
    channels.send("whatsapp", "+91-90000-00007", MESSAGE, invoice_id="INV-1",
                  rung=2, today=TODAY, enabled=True)
    actions = [e["action"] for e in audit.entries()]
    assert actions == [channels.WOULD_SEND, channels.WOULD_SEND]


# ============================================================================
# send_consolidated -- one physical send, N audit rows (one per invoice)
# ============================================================================

BUNDLE = {"INV-A": 2, "INV-B": 3}


def consolidated(**kwargs):
    defaults = dict(buyer_id="BUY-07", today=TODAY, now=datetime(2026, 8, 24, 11, 0))
    defaults.update(kwargs)
    return channels.send_consolidated("email", BUYER_ADDRESS, MESSAGE,
                                      invoice_rungs=BUNDLE, **defaults)


def test_a_consolidated_send_is_one_real_envelope(captured) -> None:
    """Non-negotiable #4 does not relax just because several invoices share
    the envelope: still exactly one socket open, one message, one recipient."""
    records = consolidated(enabled=True)
    assert len(captured) == 1
    assert captured[0]["To"] == INBOX
    assert BUYER_ADDRESS not in str(captured[0]["To"])
    assert len(records) == 2


def test_a_consolidated_send_logs_one_audit_entry_per_invoice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    consolidated(enabled=False)
    entries = {e["invoice_id"]: e for e in audit.entries()}
    assert set(entries) == set(BUNDLE)
    for inv_id, rung in BUNDLE.items():
        assert entries[inv_id]["detail"]["rung"] == rung
        assert set(entries[inv_id]["detail"]["bundle_invoice_ids"]) == set(BUNDLE)


def test_a_consolidated_send_carries_each_invoices_own_rung(captured) -> None:
    """Escalated-tier bundles can mix rung 2 and rung 3 -- each invoice's
    audit record must show ITS OWN rung, not the bundle's or a shared one."""
    records = consolidated(enabled=True)
    by_invoice = {r["invoice_id"]: r for r in records}
    assert by_invoice["INV-A"]["rung"] == 2
    assert by_invoice["INV-B"]["rung"] == 3


def test_the_bundle_is_skipped_only_when_every_invoice_was_already_sent(
    captured,
) -> None:
    """Re-running after an interrupt must not duplicate a real email -- but
    only once EVERY invoice in the bundle has actually gone out; an un-sent
    invoice must never be silently dropped just because its bundle-mate
    already went through."""
    first = consolidated(enabled=True)
    assert all(r["status"] == channels.SENT for r in first)
    assert len(captured) == 1

    second = consolidated(enabled=True)
    assert all(r["status"] == channels.SKIPPED for r in second)
    assert len(captured) == 1        # no second envelope opened


def test_a_partially_sent_bundle_is_not_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """One invoice already sent today (e.g. from a smaller earlier bundle),
    one not -- the bundle must still go out rather than silently drop
    coverage of the un-sent invoice."""
    monkeypatch.setattr(channels.smtplib, "SMTP", Capture)
    Capture.sent = []
    channels.send("email", BUYER_ADDRESS, MESSAGE, invoice_id="INV-A", buyer_id="BUY-07",
                  rung=2, today=TODAY, now=datetime(2026, 8, 24, 11, 0), enabled=True)
    assert len(Capture.sent) == 1

    records = consolidated(enabled=True)
    assert all(r["status"] == channels.SENT for r in records)
    assert len(Capture.sent) == 2


def test_a_consolidated_stub_channel_never_opens_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    records = channels.send_consolidated(
        "whatsapp", "+91-90000-00007", MESSAGE, invoice_rungs=BUNDLE,
        buyer_id="BUY-07", today=TODAY, enabled=True,
    )
    assert all(r["status"] == channels.WOULD_SEND and r["to"] is None for r in records)


def test_an_empty_bundle_sends_nothing_and_logs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channels.smtplib, "SMTP", Explode)
    records = channels.send_consolidated("email", BUYER_ADDRESS, MESSAGE, invoice_rungs={},
                                         buyer_id="BUY-07", today=TODAY, enabled=True)
    assert records == []
    assert audit.entries() == []
