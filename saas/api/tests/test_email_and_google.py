"""Email codes (sign-in, verification, password reset), the outbox, Google
sign-in, and creating a business. No test touches the network: SMTP is off
(conftest) and Google's token exchange is replaced with a stub."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app import google, mailer, otp
from conftest import register

PASSWORD = "correct horse 9"


def last_code(to: str) -> str:
    """The 6-digit code in the newest email to `to` (captured, not sent)."""
    for mail in reversed(mailer.DEV_SENT):
        if mail["to"] == to:
            return re.search(r"\b(\d{6})\b", mail["text"]).group(1)
    raise AssertionError(f"no email to {to}")


# --------------------------------------------------------------------------
# verification + sign-in codes
# --------------------------------------------------------------------------

def test_sign_up_sends_a_verification_code_and_the_code_verifies(client) -> None:
    me = register(client, "a@example.com", "Alpha Works")
    assert me["user"]["email_verified"] is False
    code = last_code("a@example.com")
    assert "confirm" in mailer.DEV_SENT[-1]["text"].lower()
    assert client.post("/auth/verify-email", json={"code": code}).status_code == 200
    assert client.get("/auth/session").json()["user"]["email_verified"] is True


def test_signing_in_with_an_email_code(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    client.post("/auth/logout")
    assert client.post("/auth/code/request", json={"email": "a@example.com"}).status_code == 202
    res = client.post("/auth/code/verify", json={"email": "a@example.com", "code": last_code("a@example.com")})
    assert res.status_code == 200, res.text
    assert res.json()["active_business"]["name"] == "Alpha Works"
    assert client.get("/dashboard").status_code == 200


def test_a_code_works_once_and_a_wrong_code_fails(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    client.post("/auth/code/request", json={"email": "a@example.com"})
    code = last_code("a@example.com")
    wrong = "000000" if code != "000000" else "111111"
    assert client.post("/auth/code/verify", json={"email": "a@example.com", "code": wrong}).status_code == 401
    assert client.post("/auth/code/verify", json={"email": "a@example.com", "code": code}).status_code == 200
    assert client.post("/auth/code/verify", json={"email": "a@example.com", "code": code}).status_code == 401


def test_five_wrong_guesses_burn_the_code(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    client.post("/auth/code/request", json={"email": "a@example.com"})
    code = last_code("a@example.com")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(otp.MAX_ATTEMPTS):
        client.post("/auth/code/verify", json={"email": "a@example.com", "code": wrong})
    assert client.post("/auth/code/verify", json={"email": "a@example.com", "code": code}).status_code == 401


def test_an_expired_code_fails(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    client.post("/auth/code/request", json={"email": "a@example.com"})
    code = last_code("a@example.com")
    from app import db
    from app.models import EmailCode
    with next(client.app.dependency_overrides[db.get_db]()) as session:
        for row in session.query(EmailCode).all():
            row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    assert client.post("/auth/code/verify", json={"email": "a@example.com", "code": code}).status_code == 401


def test_requesting_codes_is_rate_limited(client) -> None:
    register(client, "a@example.com", "Alpha Works")    # 1 verify code, a different purpose
    for _ in range(otp.MAX_PER_WINDOW):
        assert client.post("/auth/code/request", json={"email": "a@example.com"}).status_code == 202
    assert client.post("/auth/code/request", json={"email": "a@example.com"}).status_code == 429


def test_code_requests_do_not_reveal_whether_an_account_exists(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    known = client.post("/auth/code/request", json={"email": "a@example.com"})
    before = len(mailer.DEV_SENT)
    unknown = client.post("/auth/code/request", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(mailer.DEV_SENT) == before                  # ...but nobody was emailed


def test_password_reset_by_code(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    client.post("/auth/logout")
    client.post("/auth/code/request", json={"email": "a@example.com", "purpose": "reset"})
    res = client.post("/auth/password/reset", json={
        "email": "a@example.com", "code": last_code("a@example.com"), "new_password": "a brand new one"})
    assert res.status_code == 200, res.text
    client.post("/auth/logout")
    assert client.post("/auth/login", json={"email": "a@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/auth/login", json={"email": "a@example.com", "password": "a brand new one"}).status_code == 200


def test_codes_are_stored_only_as_hashes(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    code = last_code("a@example.com")
    from app import db
    from app.models import EmailCode
    with next(client.app.dependency_overrides[db.get_db]()) as session:
        assert all(code not in row.code_hash for row in session.query(EmailCode).all())


def test_every_email_goes_through_the_outbox(client) -> None:
    register(client, "a@example.com", "Alpha Works")
    from app import db
    from app.models import OutboxEmail
    with next(client.app.dependency_overrides[db.get_db]()) as session:
        rows = session.query(OutboxEmail).all()
        assert [(r.to_email, r.kind, r.status) for r in rows] == [("a@example.com", "verify_code", "sent")]


# --------------------------------------------------------------------------
# Google
# --------------------------------------------------------------------------

@pytest.fixture()
def google_on(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    claims = {"sub": "google-123", "email": "g@example.com", "email_verified": True, "name": "Gita"}
    seen = {}

    def fake_exchange(code, nonce):
        seen["code"], seen["nonce"] = code, nonce
        return dict(claims)

    monkeypatch.setattr(google, "exchange_code", fake_exchange)
    return claims, seen


def _google_round_trip(client):
    start = client.get("/auth/google/start", follow_redirects=False)
    assert start.status_code == 302
    location = start.headers["location"]
    assert location.startswith(google.AUTH_URL)
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fauth%2Fgoogle%2Fcallback" in location
    state = re.search(r"state=([^&]+)", location).group(1)
    return client.get(f"/auth/google/callback?code=abc&state={state}", follow_redirects=False)


def test_providers_reports_google_only_when_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    assert client.get("/auth/providers").json()["google"] is False
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    assert client.get("/auth/providers").json()["google"] is True


def test_a_new_google_user_is_created_and_sent_to_set_up_a_business(client, google_on) -> None:
    res = _google_round_trip(client)
    assert res.status_code == 302 and res.headers["location"] == "http://localhost:3000/welcome"
    me = client.get("/auth/session").json()
    assert me["user"]["email"] == "g@example.com" and me["user"]["email_verified"] is True
    assert me["user"]["has_password"] is False and me["active_business"] is None
    created = client.post("/business/create", json={"legal_name": "Gita Traders"})
    assert created.status_code == 201
    assert client.get("/auth/session").json()["active_business"]["name"] == "Gita Traders"
    assert client.get("/dashboard").status_code == 200


def test_google_links_to_an_existing_account_with_the_same_email(client, google_on) -> None:
    register(client, "g@example.com", "Existing Works")
    client.post("/auth/logout")
    res = _google_round_trip(client)
    assert res.headers["location"] == "http://localhost:3000/app"
    me = client.get("/auth/session").json()
    assert me["active_business"]["name"] == "Existing Works" and me["user"]["google_linked"] is True


def test_a_tampered_state_is_refused(client, google_on) -> None:
    client.get("/auth/google/start", follow_redirects=False)
    res = client.get("/auth/google/callback?code=abc&state=forged", follow_redirects=False)
    assert res.status_code == 302 and "/login?error=" in res.headers["location"]
    assert client.get("/auth/session").status_code == 401


def test_an_unverified_google_email_is_refused(client, google_on) -> None:
    claims, _ = google_on
    claims["email_verified"] = False
    res = _google_round_trip(client)
    assert "/login?error=" in res.headers["location"]


def test_a_google_only_account_cannot_sign_in_with_a_password(client, google_on) -> None:
    _google_round_trip(client)
    client.post("/auth/logout")
    assert client.post("/auth/login", json={"email": "g@example.com", "password": "anything12"}).status_code == 401
