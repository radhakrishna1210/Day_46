"""Buyer replies: the reader suggests, a person confirms, then it takes effect.
Tests run without an AI key, so the offline rules reader is what reads."""

from __future__ import annotations

from datetime import date

import pytest

from app import replies
from conftest import TODAY, register

DAY = date.fromisoformat(TODAY)          # Friday 2026-09-25


@pytest.mark.parametrize("text, intent, when", [
    ("boss thoda time do, 5 tarikh tak ho jayega", "promise", "2026-10-05"),
    ("Will pay tomorrow, sorry for the delay", "promise", "2026-09-26"),
    ("we'll transfer next friday", "promise", "2026-10-02"),
    ("NEFT by month end", "promise", "2026-09-30"),
    ("payment in 10 days", "promise", "2026-10-05"),
    ("material mein problem thi, 12 units damage the -- pehle credit note bhejo", "dispute", None),
    ("We will not pay this, stop sending reminders", "refusal", None),
    ("Can you send the invoice copy again?", "question", None),
    ("ok", "noise", None),
])
def test_offline_reader(text, intent, when) -> None:
    got = replies.offline(text, DAY)
    assert (got["intent"], got["date"]) == (intent, when), got
    assert got["source"] == "rule"


def test_offline_reader_applies_the_engine_bounds() -> None:
    far = replies.offline("will pay on 15/12/2027", DAY)
    assert far["intent"] == "question" and "limit" in far["downgraded"][0]
    greedy = replies.offline("will pay Rs 50 lakh tomorrow", DAY, outstanding_paise=100_000_00)
    assert greedy["intent"] == "question"
    part = replies.offline("half payment by 30th", DAY)
    assert part["intent"] == "promise" and part["amount"] == "partial"


def test_flags_for_a_human(monkeypatch) -> None:
    got = replies.read("goods were damaged, but Rs 1 lakh Friday and Rs 4 lakh next month", DAY)
    assert got["reader"] == "rules" and got["intent"] == "dispute"
    assert got["date"] is None                      # a dispute outranks the payment offer
    promise = replies.read("Rs 1 lakh on 30th and remaining Rs 2 lakh later", DAY)
    assert any("more than one amount" in f for f in promise["flags"])


def _open_invoice(client) -> str:
    register(client, "owner@example.com", "Alpha Works")
    client.post("/business/demo-data")
    return next(i["id"] for i in client.get("/invoices").json() if i["status"] == "overdue")


def test_read_then_confirm_a_promise(client) -> None:
    inv = _open_invoice(client)
    text = "boss thoda time do, 5 tarikh tak ho jayega"
    s = client.post(f"/invoices/{inv}/replies/read", json={"text": text}).json()
    assert s["intent"] == "promise" and s["date"] == "2026-10-05" and s["reader"] == "rules"
    assert client.get(f"/invoices/{inv}").json()["promises"] == []      # reading changes nothing

    res = client.post(f"/invoices/{inv}/replies", json={
        "text": text, "intent": "promise", "promised_date": s["date"], "channel": "whatsapp",
        "suggested_intent": s["intent"], "suggested_by": s["reader"]})
    assert res.status_code == 201, res.text
    detail = res.json()
    assert detail["promises"][-1]["promised_date"] == "2026-10-05"
    assert detail["replies"][0]["intent"] == "promise" and detail["replies"][0]["suggested_by"] == "rules"

    trail = client.get("/audit").json()["entries"]
    read = next(e for e in trail if e["action"] == "reply_read")
    recorded = next(e for e in trail if e["action"] == "reply_recorded")
    assert read["source"] == "rule" and read["actor"] == "agent"
    assert recorded["source"] == "user" and "the rules read it as promise; confirmed" in recorded["reason"]


def test_a_person_can_overrule_the_reader(client) -> None:
    inv = _open_invoice(client)
    res = client.post(f"/invoices/{inv}/replies", json={
        "text": "will pay tomorrow but 3 boxes were broken", "intent": "dispute",
        "suggested_intent": "promise", "suggested_by": "rules"})
    assert res.status_code == 201 and res.json()["disputed"] is True
    recorded = next(e for e in client.get("/audit").json()["entries"] if e["action"] == "reply_recorded")
    assert "corrected to dispute" in recorded["reason"]
    decision = client.get(f"/decisions/{inv}").json()
    assert decision["kind"] in ("handoff", "stop")               # disputes go to a person


def test_promise_needs_a_future_date(client) -> None:
    inv = _open_invoice(client)
    assert client.post(f"/invoices/{inv}/replies", json={"text": "x", "intent": "promise"}).status_code == 422
    assert client.post(f"/invoices/{inv}/replies", json={
        "text": "x", "intent": "promise", "promised_date": "2026-09-01"}).status_code == 422
