"""The business endpoints, end to end, on the engine's own demo world."""

from __future__ import annotations

import io

import pytest

from conftest import register


@pytest.fixture()
def demo(client):
    register(client, "owner@example.com", "Demo Precision Works")
    res = client.post("/business/demo-data")
    assert res.status_code == 201, res.text
    return res.json()


def test_demo_data_loads_once_into_an_empty_business(client, demo) -> None:
    assert demo["buyers"] == 20 and demo["invoices"] > 100
    assert client.post("/business/demo-data").status_code == 409


def test_dashboard_adds_up(client, demo) -> None:
    d = client.get("/dashboard").json()
    t = d["totals"]
    assert t["overdue_paise"] <= t["receivable_paise"]
    assert sum(b["paise"] for b in d["aging"]) == t["overdue_paise"]
    assert sum(b["count"] for b in d["aging"]) == t["overdue_invoices"] > 0
    assert t["interest_accrued_paise"] > 0
    assert d["top_buyers"] == sorted(d["top_buyers"], key=lambda b: -b["overdue_paise"])
    assert len(d["collections"]) == 12


def test_the_decision_queue_is_the_engines_and_never_sends(client, demo) -> None:
    q = client.get("/decisions").json()
    kinds = {r["kind"] for r in q["decisions"]}
    assert kinds <= {"send", "wait", "handoff", "stop", "payment_plan", "counter_settle"}
    assert "send" in kinds
    for row in q["decisions"]:
        assert row["reason"]                                  # every decision explains itself
        assert row["rung"] == 0 or 1 <= row["rung"] <= row["available_rung"]  # law ceiling
    assert sum(q["tally"].values()) == len(q["decisions"])


def test_a_send_decision_comes_with_a_guardrailed_draft_signed_by_this_business(client, demo) -> None:
    send = next(r for r in client.get("/decisions").json()["decisions"] if r["kind"] == "send")
    full = client.get(f"/decisions/{send['invoice_id']}").json()
    body = full["draft"]["body"]
    assert full["draft"]["subject"] and body
    # Signed by THIS business's contact (the owner's name from sign-up, "Owner"
    # here), never config/supplier.yaml's demo placeholder.
    assert body.rstrip().endswith("Owner")
    assert "A. Placeholder" not in body and "Example Precision Works" not in body


def test_approving_logs_the_contact_and_the_next_decision_respects_spacing(client, demo) -> None:
    send = next(r for r in client.get("/decisions").json()["decisions"] if r["kind"] == "send")
    res = client.post(f"/decisions/{send['invoice_id']}/approve", json={"channel": "phone"})
    assert res.status_code == 201, res.text
    again = client.get(f"/decisions/{send['invoice_id']}").json()
    assert again["kind"] != "send"                       # spacing rule: not twice in one day
    assert client.post(f"/decisions/{send['invoice_id']}/approve", json={}).status_code == 409
    audit = client.get("/audit", params={"action": "reminder_approved"}).json()["entries"]
    assert audit and audit[0]["invoice_number"] == send["invoice_number"]


def test_a_promise_quiets_the_agent_and_a_dispute_hands_off(client, demo) -> None:
    send = next(r for r in client.get("/decisions").json()["decisions"] if r["kind"] == "send")
    inv = send["invoice_id"]
    client.post(f"/invoices/{inv}/promises", json={"promised_date": "2026-10-05"})
    assert client.get(f"/decisions/{inv}").json()["kind"] == "wait"
    client.post(f"/invoices/{inv}/dispute", json={"disputed": True, "note": "goods damaged"})
    assert client.get(f"/decisions/{inv}").json()["kind"] == "handoff"


def test_a_full_payment_settles_the_invoice_and_overpayment_is_refused(client, demo) -> None:
    send = next(r for r in client.get("/decisions").json()["decisions"] if r["kind"] == "send")
    detail = client.get(f"/invoices/{send['invoice_id']}").json()
    owed = detail["outstanding_paise"]
    over = client.post(f"/invoices/{send['invoice_id']}/payments",
                       json={"paid_on": "2026-09-25", "amount_paise": owed + 1})
    assert over.status_code == 422
    paid = client.post(f"/invoices/{send['invoice_id']}/payments",
                       json={"paid_on": "2026-09-25", "amount_paise": owed}).json()
    assert paid["status"] == "paid" and paid["outstanding_paise"] == 0
    assert send["invoice_id"] not in {r["invoice_id"]
                                      for r in client.get("/decisions").json()["decisions"]}


def test_csv_import_creates_rows_and_reports_bad_ones(client) -> None:
    register(client, "owner@example.com", "Importer Ltd")
    csv_text = (
        "invoice_number,buyer_name,amount_rupees,issue_date,acceptance_date,"
        "written_agreement,agreed_days,description,po_number\n"
        "INV-1,Vistara Traders,\"1,25,000.50\",2026-07-01,2026-07-03,yes,30,Brackets,PO-1\n"
        "INV-2,Vistara Traders,5000,2026-07-10,2026-07-10,no,,Bolts,\n"
        "INV-3,New Buyer Co,-5,2026-07-10,2026-07-10,no,,,\n"
        "INV-4,New Buyer Co,100,2026-07-10,2026-07-01,no,,,\n"
        "INV-1,Vistara Traders,100,2026-07-10,2026-07-10,no,,,\n")
    res = client.post("/invoices/import",
                      files={"file": ("book.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["created"] == 2 and body["new_buyers"] == 1
    assert [e["line"] for e in body["skipped"]] == [4, 5, 6]
    amounts = {i["invoice_number"]: i["amount_paise"] for i in client.get("/invoices").json()}
    assert amounts == {"INV-1": 12_500_050, "INV-2": 500_000}


def test_the_audit_trail_records_who_did_what_and_cannot_be_edited(client, demo) -> None:
    entries = client.get("/audit").json()["entries"]
    actions = {e["action"] for e in entries}
    assert {"business_created", "demo_data_loaded"} <= actions
    assert all(e["actor"] == "owner@example.com" for e in entries)
    entry_id = entries[0]["id"]
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"/audit/{entry_id}").status_code in (404, 405)


def test_legal_figures_are_served_from_config_not_typed_into_the_ui(client) -> None:
    """The web app quotes statutory numbers only from this endpoint, which reads
    config/legal.yaml through the engine -- non-negotiable #3."""
    from engine.config import legal
    figures = client.get("/meta/legal").json()
    assert figures["no_agreement_days"] == legal()["no_agreement_days"]
    assert figures["max_agreement_days"] == legal()["max_agreement_days"]
    assert figures["bank_rate_multiplier"] == legal()["bank_rate_multiplier"]


def test_early_warnings_name_the_buyer_and_explain_themselves(client, demo) -> None:
    for w in client.get("/dashboard").json()["early_warnings"]:
        assert w["buyer"] and w["reasons"] and w["risk_band"] in ("watch", "high")


def test_business_profile_drives_the_udyam_flag(client) -> None:
    register(client, "owner@example.com", "Profile Co")
    assert client.get("/business").json()["udyam_on_file"] is False
    res = client.put("/business", json={"legal_name": "Profile Co Pvt Ltd",
                                        "udyam_registration": "UDYAM-KR-03-0012345",
                                        "enterprise_class": "micro"})
    assert res.json()["udyam_on_file"] is True and res.json()["msmed_covered"] is True
