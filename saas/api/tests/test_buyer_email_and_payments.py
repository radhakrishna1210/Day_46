"""Emailing a reminder to a buyer (a person clicks Send; the rules re-decide),
and Razorpay payment links. Razorpay is faked -- no test calls the network."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app import mailer, payments
from conftest import register

SECRET = "whsec_test_only"


@pytest.fixture()
def book(client):
    register(client, "owner@example.com", "Alpha Works")
    client.post("/business/demo-data")
    send = next(r for r in client.get("/decisions").json()["decisions"] if r["kind"] == "send")
    buyer_id = send["buyer"]["id"]
    buyer = client.get(f"/buyers/{buyer_id}").json()
    client.put(f"/buyers/{buyer_id}", json={**{k: buyer[k] for k in (
        "name", "profile", "sector", "language_pref", "contact_name", "contact_phone", "city",
        "state", "gstin", "preferred_channel", "opted_out")}, "contact_email": "ap@buyer.example"})
    return send


class FakeRazorpay:
    def __init__(self):
        self.links: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def post(self, path, body):
        self.calls.append(("POST", path))
        if path.endswith("/cancel"):
            self.links[path.split("/")[2]]["status"] = "cancelled"
            return {}
        pid = f"plink_{len(self.links) + 1:04d}"
        self.links[pid] = {"id": pid, "short_url": f"https://rzp.io/i/{pid}", "status": "created",
                           "amount": body["amount"], "amount_paid": 0, "payments": [], "body": body}
        return self.links[pid]

    def get(self, path):
        self.calls.append(("GET", path))
        return self.links[path.split("/")[2]]

    def pay(self, pid):
        link = self.links[pid]
        link.update(status="paid", amount_paid=link["amount"], payments=[{"payment_id": "pay_T1"}])


@pytest.fixture()
def rzp(monkeypatch):
    fake = FakeRazorpay()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(payments, "_post", fake.post)
    monkeypatch.setattr(payments, "_get", fake.get)
    return fake


# --------------------------------------------------------------------------
# buyer email
# --------------------------------------------------------------------------

def test_sending_emails_the_guardrailed_draft_and_logs_the_contact(client, book) -> None:
    inv = book["invoice_id"]
    draft = client.get(f"/decisions/{inv}").json()["draft"]
    mailer.DEV_SENT.clear()
    res = client.post(f"/decisions/{inv}/send-email")
    assert res.status_code == 201, res.text
    mail = mailer.DEV_SENT[-1]
    assert mail["to"] == "ap@buyer.example"
    assert mail["subject"] == draft["subject"] and draft["body"].strip()[:80] in mail["text"]
    assert mail["from_name"] == "Alpha Works via Recova" and mail["reply_to"] == "owner@example.com"
    assert "rzp.io" not in mail["text"]                      # payments off: no link
    detail = client.get(f"/invoices/{inv}").json()
    assert detail["contacts"][-1]["channel"] == "email"
    entry = client.get("/audit", params={"action": "reminder_emailed"}).json()["entries"][0]
    assert entry["source"] == "rule" and "approved by owner@example.com" in entry["reason"]
    assert client.post(f"/decisions/{inv}/send-email").status_code == 409     # not twice a day


def test_no_email_address_opt_out_and_viewers_are_refused(client, book) -> None:
    inv = book["invoice_id"]
    buyer_id = book["buyer"]["id"]
    buyer = client.get(f"/buyers/{buyer_id}").json()
    fields = {k: buyer[k] for k in ("name", "profile", "sector", "language_pref", "contact_name",
                                    "contact_phone", "city", "state", "gstin", "preferred_channel")}
    client.put(f"/buyers/{buyer_id}", json={**fields, "contact_email": None, "opted_out": False})
    assert "email address" in client.post(f"/decisions/{inv}/send-email").json()["detail"]
    client.put(f"/buyers/{buyer_id}", json={**fields, "contact_email": "ap@buyer.example", "opted_out": True})
    assert "opted out" in client.post(f"/decisions/{inv}/send-email").json()["detail"]


def test_a_blocked_email_logs_nothing(client, book, monkeypatch) -> None:
    monkeypatch.setattr(mailer, "_send_smtp", lambda row: None)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("RECOVA_EMAIL_ALLOWLIST", "me@mine.com")
    inv = book["invoice_id"]
    res = client.post(f"/decisions/{inv}/send-email")
    assert res.status_code == 502 and "ALLOWLIST" in res.json()["detail"]
    assert client.get(f"/invoices/{inv}").json()["contacts"] == []
    assert client.get("/decisions").json()["decisions"]           # still decidable
    assert client.get(f"/decisions/{inv}").json()["kind"] == "send"


# --------------------------------------------------------------------------
# Razorpay
# --------------------------------------------------------------------------

def test_the_reminder_carries_a_payment_link_and_checking_records_the_money(client, book, rzp) -> None:
    inv = book["invoice_id"]
    owed = client.get(f"/invoices/{inv}").json()["outstanding_paise"]
    mailer.DEV_SENT.clear()
    assert client.post(f"/decisions/{inv}/send-email").status_code == 201
    assert "https://rzp.io/i/plink_0001" in mailer.DEV_SENT[-1]["text"]
    sent = rzp.links["plink_0001"]["body"]
    assert sent["amount"] == owed and sent["notify"] == {"sms": False, "email": False}

    rzp.pay("plink_0001")
    res = client.post(f"/invoices/{inv}/payment-link/check").json()
    assert res["link_status"] == "paid" and res["outstanding_paise"] == 0
    assert res["payments"][-1]["note"] == "Razorpay pay_T1"
    entry = client.get("/audit", params={"action": "payment_received_online"}).json()["entries"][0]
    assert entry["actor"] == "razorpay"
    assert client.post(f"/invoices/{inv}/payment-link/check").status_code == 404   # nothing open


def test_links_are_reused_then_replaced_when_the_amount_changes(client, book, rzp) -> None:
    inv = book["invoice_id"]
    a = client.post(f"/invoices/{inv}/payment-link").json()["payment_link"]
    b = client.post(f"/invoices/{inv}/payment-link").json()["payment_link"]
    assert a["url"] == b["url"] and len(rzp.links) == 1
    client.post(f"/invoices/{inv}/payments", json={"paid_on": "2026-09-25", "amount_paise": 100_00})
    c = client.post(f"/invoices/{inv}/payment-link").json()["payment_link"]
    assert c["url"] != a["url"] and rzp.links["plink_0001"]["status"] == "cancelled"
    assert c["amount_paise"] == a["amount_paise"] - 100_00


def _webhook(client, body: dict, secret: str = SECRET):
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/razorpay", content=raw,
                       headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})


def test_the_webhook_is_signed_and_idempotent(client, book, rzp) -> None:
    inv = book["invoice_id"]
    link = client.post(f"/invoices/{inv}/payment-link").json()["payment_link"]
    event = {"event": "payment_link.paid", "payload": {
        "payment_link": {"entity": {"id": "plink_0001", "amount_paid": link["amount_paise"]}},
        "payment": {"entity": {"id": "pay_W1", "amount": link["amount_paise"]}}}}
    assert _webhook(client, event, secret="wrong").status_code == 401
    assert client.get(f"/invoices/{inv}").json()["payments"] == []
    assert _webhook(client, event).json()["result"] == "recorded"
    assert _webhook(client, event).json()["result"] == "already recorded"
    detail = client.get(f"/invoices/{inv}").json()
    assert len(detail["payments"]) == 1 and detail["outstanding_paise"] == 0


def test_no_keys_means_no_links(client, book) -> None:
    inv = book["invoice_id"]
    assert client.get(f"/invoices/{inv}").json()["payments_enabled"] is False
    assert client.post(f"/invoices/{inv}/payment-link").status_code == 409
