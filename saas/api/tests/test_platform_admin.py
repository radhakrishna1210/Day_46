"""The platform super admin: granted only by RECOVA_SUPER_ADMIN_EMAILS plus a
verified email, invisible (404) to everyone else, and read-only counts -- never
another business's buyers or invoices."""

from __future__ import annotations

from conftest import register
from test_email_and_google import last_code

ADMIN = "boss@example.com"


def _verify(client, email: str) -> None:
    assert client.post("/auth/verify-email", json={"code": last_code(email)}).status_code == 200


def test_nobody_is_super_admin_by_default(client) -> None:
    register(client, ADMIN, "Boss Works")
    _verify(client, ADMIN)
    assert client.get("/auth/session").json()["user"]["is_super_admin"] is False
    assert client.get("/platform/overview").status_code == 404


def test_listed_and_verified_email_is_super_admin(client, monkeypatch) -> None:
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", f"someone@else.com, {ADMIN.upper()}")
    register(client, ADMIN, "Boss Works")
    _verify(client, ADMIN)
    assert client.get("/auth/session").json()["user"]["is_super_admin"] is True
    res = client.get("/platform/overview")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["totals"]["businesses"] == 1 and body["totals"]["users"] == 1
    assert body["businesses"][0]["owner_email"] == ADMIN


def test_an_unverified_listed_email_gets_nothing(client, monkeypatch) -> None:
    """Registering the admin's address with a password must not grant it."""
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", ADMIN)
    register(client, ADMIN, "Impostor Ltd")
    assert client.get("/auth/session").json()["user"]["is_super_admin"] is False
    assert client.get("/platform/overview").status_code == 404


def test_business_owners_cannot_reach_the_platform_area(client, second_client, monkeypatch) -> None:
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", ADMIN)
    register(second_client, "owner@example.com", "Owner Works")
    _verify(second_client, "owner@example.com")
    assert second_client.get("/platform/overview").status_code == 404
    second_client.post("/auth/logout")
    second_client.cookies.clear()
    assert second_client.get("/platform/overview").status_code == 401


def test_overview_counts_every_business_but_carries_no_ledger_detail(client, second_client,
                                                                     monkeypatch) -> None:
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", ADMIN)
    register(second_client, "owner@example.com", "Owner Works")
    assert second_client.post("/business/demo-data").status_code == 201
    register(client, ADMIN, "Boss Works")
    _verify(client, ADMIN)

    body = client.get("/platform/overview").json()
    owner_works = next(b for b in body["businesses"] if b["name"] == "Owner Works")
    assert owner_works["buyers"] > 0 and owner_works["invoices"] > 0
    assert body["totals"]["invoices"] == owner_works["invoices"]
    text = str(body)
    assert "INV-" not in text and "amount_paise" not in text and "outstanding" not in text
