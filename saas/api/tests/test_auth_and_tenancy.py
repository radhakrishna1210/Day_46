"""Accounts, sessions, and the one property a multi-tenant app cannot get
wrong: one business never sees or touches another's data."""

from __future__ import annotations

from conftest import register


def _buyer(client, name="Acme Traders"):
    res = client.post("/buyers", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


def _invoice(client, buyer_id, number="INV-1"):
    res = client.post("/invoices", json={
        "buyer_id": buyer_id, "invoice_number": number, "amount_paise": 50_000_00,
        "issue_date": "2026-06-01", "acceptance_date": "2026-06-02",
        "written_agreement": True, "agreed_days": 30})
    assert res.status_code == 201, res.text
    return res.json()


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def test_register_creates_a_business_and_signs_in(client) -> None:
    me = register(client, "a@example.com", "Alpha Works")
    assert me["user"]["email"] == "a@example.com"
    assert me["active_business"]["name"] == "Alpha Works"
    assert me["active_business"]["role"] == "owner"
    assert client.get("/auth/session").json()["active_business"]["name"] == "Alpha Works"


def test_the_password_is_never_stored_or_returned_in_plain_text(client) -> None:
    me = register(client, "a@example.com", "Alpha Works")
    # Neither the password nor its hash is ever returned (a has_password flag is fine).
    assert "correct horse" not in str(me) and "scrypt$" not in str(me)
    assert "password_hash" not in str(me)
    from app import db
    from app.models import User
    with next(client.app.dependency_overrides[db.get_db]()) as session:
        user = session.query(User).one()
        assert "correct horse" not in user.password_hash
        assert user.password_hash.startswith("scrypt$")


def test_duplicate_email_is_refused(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    res = client.post("/auth/register", json={"name": "X", "email": "A@example.com",
                                              "password": "another pass 1",
                                              "business_name": "Other"})
    assert res.status_code == 409


def test_wrong_password_is_refused_and_logout_ends_the_session(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/session").status_code == 401
    bad = client.post("/auth/login", json={"email": "a@example.com", "password": "nope nope"})
    assert bad.status_code == 401
    good = client.post("/auth/login", json={"email": "a@example.com",
                                            "password": "correct horse 9"})
    assert good.status_code == 200
    assert good.json()["active_business"]["name"] == "Alpha Works"


def test_everything_tenant_scoped_needs_a_session(client) -> None:
    for path in ("/buyers", "/invoices", "/dashboard", "/decisions", "/audit", "/business"):
        assert client.get(path).status_code == 401, path


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------

def test_one_business_never_sees_anothers_rows(client, second_client) -> None:
    register(client, "a@example.com", "Alpha Works")
    buyer = _buyer(client, "Alpha's Customer")
    invoice = _invoice(client, buyer["id"])

    register(second_client, "b@example.com", "Beta Industries")
    assert second_client.get("/buyers").json() == []
    assert second_client.get("/invoices").json() == []
    assert second_client.get("/decisions").json()["decisions"] == []
    assert all("Alpha" not in (e["reason"] + str(e["buyer_name"]))
               for e in second_client.get("/audit").json()["entries"])
    # Direct ids from the other business read as "not found" -- never "forbidden",
    # which would confirm the row exists.
    assert second_client.get(f"/buyers/{buyer['id']}").status_code == 404
    assert second_client.get(f"/invoices/{invoice['id']}").status_code == 404
    assert second_client.get(f"/decisions/{invoice['id']}").status_code == 404


def test_one_business_can_never_write_to_anothers_rows(client, second_client) -> None:
    register(client, "a@example.com", "Alpha Works")
    buyer = _buyer(client)
    invoice = _invoice(client, buyer["id"])
    register(second_client, "b@example.com", "Beta Industries")

    attempts = [
        second_client.put(f"/buyers/{buyer['id']}", json={"name": "hijacked"}),
        second_client.delete(f"/buyers/{buyer['id']}"),
        second_client.delete(f"/invoices/{invoice['id']}"),
        second_client.post(f"/invoices/{invoice['id']}/payments",
                           json={"paid_on": "2026-09-01", "amount_paise": 100}),
        second_client.post(f"/invoices/{invoice['id']}/dispute", json={"disputed": True}),
        second_client.post(f"/decisions/{invoice['id']}/approve", json={}),
        # Attaching an invoice to someone else's buyer is refused too.
        second_client.post("/invoices", json={
            "buyer_id": buyer["id"], "invoice_number": "X", "amount_paise": 1,
            "issue_date": "2026-06-01", "acceptance_date": "2026-06-01"}),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    # ...and Alpha's data is untouched.
    assert client.get(f"/buyers/{buyer['id']}").json()["name"] == "Acme Traders"
    assert client.get(f"/invoices/{invoice['id']}").json()["paid_paise"] == 0


def test_switching_to_a_business_you_do_not_belong_to_is_refused(client, second_client) -> None:
    alpha = register(client, "a@example.com", "Alpha Works")
    register(second_client, "b@example.com", "Beta Industries")
    res = second_client.post("/auth/switch",
                             json={"tenant_id": alpha["active_business"]["id"]})
    assert res.status_code == 404
