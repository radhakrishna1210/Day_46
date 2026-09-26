"""The daily run + morning digest."""

from __future__ import annotations

from datetime import date

from app import digest, mailer
from app import db as db_module
from app.main import app
from conftest import TODAY, register
from test_email_and_google import last_code
from test_team import invite_link, join

DAY = date.fromisoformat(TODAY)


def _db():
    """The test database the app is using (conftest overrides get_db)."""
    return next(app.dependency_overrides[db_module.get_db]())


def _verified_owner(client, email="owner@example.com", business="Alpha Works") -> None:
    register(client, email, business)
    assert client.post("/auth/verify-email", json={"code": last_code(email)}).status_code == 200
    assert client.post("/business/demo-data").status_code == 201


def _digests() -> list[dict]:
    return [m for m in mailer.DEV_SENT if "Open today's decisions" in m["text"]]


def test_digest_matches_the_decision_queue(client) -> None:
    _verified_owner(client)
    preview = client.get("/digest").json()
    queue = client.get("/decisions").json()
    acts = sum(n for k, n in queue["tally"].items() if k in digest.ACT_KINDS)
    assert preview["summary"]["to_act"] == acts
    assert preview["summary"]["overdue_invoices"] == len(queue["decisions"])
    assert preview["recipients"] == ["owner@example.com"] and preview["would_send"] is True


def test_daily_run_emails_the_team_once_and_audits_it(client, second_client) -> None:
    _verified_owner(client)
    client.post("/team/invites", json={"email": "view@example.com", "role": "viewer"})
    join(second_client, "view@example.com", invite_link("view@example.com"))
    mailer.DEV_SENT.clear()

    db = _db()
    ran = digest.run_daily(db, DAY)
    assert len(ran) == 1 and ran[0]["emailed"] == ["owner@example.com"]   # viewers: no digest
    mails = _digests()
    assert len(mails) == 1 and mails[0]["to"] == "owner@example.com"
    assert "Alpha Works" in mails[0]["subject"]

    assert digest.run_daily(db, DAY) == []            # same day again: nothing
    assert len(_digests()) == 1
    audit_rows = [e for e in client.get("/audit").json()["entries"] if e["action"] == "daily_run"]
    assert len(audit_rows) == 1 and audit_rows[0]["source"] == "rule"


def test_opting_out_and_unverified_people_get_nothing(client) -> None:
    _verified_owner(client)
    me = client.put("/auth/preferences", json={"digest_opt_out": True}).json()
    assert me["user"]["digest_opt_out"] is True
    mailer.DEV_SENT.clear()
    ran = digest.run_daily(_db(), DAY)
    assert ran[0]["emailed"] == [] and _digests() == []


def test_send_me_now_does_not_count_as_the_daily_run(client) -> None:
    _verified_owner(client)
    mailer.DEV_SENT.clear()
    assert client.post("/digest/send-me").status_code == 200
    assert len(_digests()) == 1
    assert client.get("/digest").json()["last_daily_run"] is None
    assert len(digest.run_daily(_db(), DAY)) == 1


def test_suspended_businesses_are_skipped_and_empty_days_send_nothing(client, second_client,
                                                                     monkeypatch) -> None:
    _verified_owner(client)
    register(second_client, "quiet@example.com", "Quiet Ltd")      # no invoices at all
    second_client.post("/auth/verify-email", json={"code": last_code("quiet@example.com")})
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", "owner@example.com")
    alpha = next(b["id"] for b in client.get("/platform/overview").json()["businesses"]
                 if b["name"] == "Alpha Works")
    client.post(f"/platform/businesses/{alpha}/suspend")
    mailer.DEV_SENT.clear()

    ran = digest.run_daily(_db(), DAY)
    assert [r["tenant"] for r in ran] == ["Quiet Ltd"] and ran[0]["emailed"] == []
    assert _digests() == []


def test_the_allow_list_holds_back_real_email_to_anyone_else(client, monkeypatch) -> None:
    """With SMTP on and RECOVA_EMAIL_ALLOWLIST set, only listed addresses are
    sent to; the rest are recorded as blocked. (SMTP itself is faked here.)"""
    sent: list[str] = []
    monkeypatch.setattr(mailer, "_send_smtp", lambda row: sent.append(row.to_email))
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("RECOVA_EMAIL_ALLOWLIST", "me@mine.com, @myco.in")
    db = _db()
    rows = [mailer.queue(db, to=to, kind="test", subject="s", text="t")
            for to in ("me@mine.com", "x@myco.in", "stranger@elsewhere.com")]
    db.commit()
    mailer.deliver_pending(db)
    assert sent == ["me@mine.com", "x@myco.in"]
    assert [r.status for r in rows] == ["sent", "sent", "blocked"]
    assert mailer.deliver_pending(db) == 0                  # blocked is final, not retried


def test_platform_run_now(client, monkeypatch) -> None:
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", "owner@example.com")
    _verified_owner(client)
    res = client.post("/platform/daily-run").json()
    assert res["ran"] == 1 and res["emailed"] == 1
    assert client.post("/platform/daily-run").json()["ran"] == 0
    assert client.post("/platform/daily-run?force=true").json()["ran"] == 1
