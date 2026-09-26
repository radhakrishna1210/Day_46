"""Team invites and the four roles: owner / admin / member / viewer."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import mailer
from app.main import app
from conftest import register

PASSWORD = "correct horse 9"


def invite_link(to: str) -> str:
    """The token in the newest invite email to `to` (captured, not sent)."""
    for mail in reversed(mailer.DEV_SENT):
        if mail["to"] == to and "/invite/" in mail["text"]:
            return re.search(r"/invite/([A-Za-z0-9_\-]+)", mail["text"]).group(1)
    raise AssertionError(f"no invite email to {to}")


def join(client: TestClient, email: str, token: str, name: str = "Teammate") -> dict:
    res = client.post("/auth/register", json={"name": name, "email": email, "password": PASSWORD,
                                              "invite_token": token})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def third_client(client):
    with TestClient(app) as c:
        yield c


def _invite(client, email: str, role: str = "member"):
    return client.post("/team/invites", json={"email": email, "role": role})


def test_invite_email_signup_joins_the_business_with_that_role(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    assert _invite(client, "Mate@Example.com", "member").status_code == 201
    token = invite_link("mate@example.com")

    info = second_client.get(f"/invites/{token}").json()
    assert info == {"business": "Alpha Works", "invited_by": "owner@example.com", "role": "member",
                    "email": "mate@example.com", "status": "pending", "has_account": False}

    me = join(second_client, "mate@example.com", token)
    assert me["active_business"]["name"] == "Alpha Works" and me["active_business"]["role"] == "member"
    assert me["user"]["email_verified"] is True         # the link proved the inbox
    assert second_client.get("/dashboard").status_code == 200
    assert second_client.get(f"/invites/{token}").json()["status"] == "accepted"
    members = {m["email"]: m["role"] for m in client.get("/team").json()["members"]}
    assert members == {"owner@example.com": "owner", "mate@example.com": "member"}


def test_an_existing_account_accepts_and_gains_a_second_business(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    register(second_client, "mate@example.com", "Mate Traders")
    _invite(client, "mate@example.com", "admin")
    res = second_client.post(f"/invites/{invite_link('mate@example.com')}/accept")
    assert res.status_code == 200, res.text
    names = {b["name"]: b["role"] for b in res.json()["businesses"]}
    assert names == {"Mate Traders": "owner", "Alpha Works": "admin"}
    assert res.json()["active_business"]["name"] == "Alpha Works"


def test_only_the_invited_email_can_accept(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    register(second_client, "someone@example.com", "Else Ltd")
    _invite(client, "mate@example.com")
    token = invite_link("mate@example.com")
    assert second_client.post(f"/invites/{token}/accept").status_code == 403
    res = second_client.post("/auth/register", json={"name": "X", "email": "other@example.com",
                                                     "password": PASSWORD, "invite_token": token})
    assert res.status_code == 403


def test_revoked_resent_and_reused_links(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    invite_id = _invite(client, "mate@example.com").json()["id"]
    old = invite_link("mate@example.com")
    assert client.post(f"/team/invites/{invite_id}/resend").status_code == 200
    new = invite_link("mate@example.com")
    assert old != new and second_client.get(f"/invites/{old}").status_code == 404
    assert client.delete(f"/team/invites/{invite_id}").status_code == 204
    assert second_client.get(f"/invites/{new}").json()["status"] == "revoked"
    res = second_client.post("/auth/register", json={"name": "M", "email": "mate@example.com",
                                                     "password": PASSWORD, "invite_token": new})
    assert res.status_code == 410


def test_viewer_reads_but_cannot_write(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    client.post("/business/demo-data")
    _invite(client, "cA@example.com", "viewer")
    join(second_client, "ca@example.com", invite_link("ca@example.com"), "The CA")
    assert second_client.get("/invoices").status_code == 200
    assert second_client.get("/decisions").status_code == 200
    blocked = second_client.post("/buyers", json={"name": "New Buyer", "profile": "corporate"})
    assert blocked.status_code == 403 and "Viewers" in blocked.json()["detail"]
    assert second_client.put("/business", json={"legal_name": "Hijack"}).status_code == 403
    assert _invite(second_client, "x@example.com").status_code == 403
    assert "invites" in second_client.get("/team").json() and second_client.get("/team").json()["invites"] == []


def test_member_works_but_cannot_manage(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    _invite(client, "mate@example.com", "member")
    join(second_client, "mate@example.com", invite_link("mate@example.com"))
    assert second_client.post("/buyers", json={"name": "New Buyer", "profile": "corporate"}).status_code == 201
    assert second_client.put("/business", json={"legal_name": "Hijack"}).status_code == 403
    assert _invite(second_client, "x@example.com").status_code == 403


def test_owner_is_protected_and_nobody_edits_themselves(client, second_client, third_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    _invite(client, "adm@example.com", "admin")
    join(second_client, "adm@example.com", invite_link("adm@example.com"))
    _invite(second_client, "mem@example.com", "member")        # admins can invite
    join(third_client, "mem@example.com", invite_link("mem@example.com"))
    ids = {m["email"]: m["id"] for m in client.get("/team").json()["members"]}

    assert second_client.put(f"/team/members/{ids['owner@example.com']}", json={"role": "viewer"}).status_code == 403
    assert second_client.delete(f"/team/members/{ids['owner@example.com']}").status_code == 403
    assert second_client.put(f"/team/members/{ids['adm@example.com']}", json={"role": "member"}).status_code == 403
    assert client.put(f"/team/members/{ids['owner@example.com']}", json={"role": "admin"}).status_code == 403
    assert client.post("/team/invites", json={"email": "o@example.com", "role": "owner"}).status_code == 422

    assert second_client.put(f"/team/members/{ids['mem@example.com']}", json={"role": "viewer"}).status_code == 200
    assert third_client.post("/buyers", json={"name": "B", "profile": "corporate"}).status_code == 403
    assert second_client.delete(f"/team/members/{ids['mem@example.com']}").status_code == 204
    assert third_client.get("/dashboard").status_code == 403


def test_transfer_ownership_and_leave(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    _invite(client, "adm@example.com", "admin")
    join(second_client, "adm@example.com", invite_link("adm@example.com"))
    ids = {m["email"]: m["id"] for m in client.get("/team").json()["members"]}

    assert client.post("/team/leave").status_code == 409              # owner can't just leave
    assert second_client.post(f"/team/members/{ids['owner@example.com']}/make-owner").status_code == 403
    assert client.post(f"/team/members/{ids['adm@example.com']}/make-owner").status_code == 200
    roles = {m["email"]: m["role"] for m in client.get("/team").json()["members"]}
    assert roles == {"owner@example.com": "admin", "adm@example.com": "owner"}

    res = client.post("/team/leave")
    assert res.status_code == 200 and res.json()["businesses"] == []
    assert client.get("/dashboard").status_code == 403


def test_inviting_someone_already_on_the_team_is_refused(client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    assert _invite(client, "owner@example.com").status_code == 409


def test_team_changes_are_audited(client, second_client) -> None:
    register(client, "owner@example.com", "Alpha Works")
    _invite(client, "mate@example.com", "viewer")
    join(second_client, "mate@example.com", invite_link("mate@example.com"))
    actions = [e["action"] for e in client.get("/audit").json()["entries"]]
    assert "member_invited" in actions and "member_joined" in actions
