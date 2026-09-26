"""Every test gets a fresh, empty database and a pinned clock."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app import db as db_module  # noqa: E402
from app.main import app  # noqa: E402

TODAY = "2026-09-25"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RECOVA_TODAY", TODAY)
    # Never send real email from tests, whatever saas/api/.env says: with
    # SMTP_HOST empty the mailer records into mailer.DEV_SENT instead.
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("RECOVA_PUBLIC_URL", "http://localhost:3000")
    # No super admin unless a test names one (saas/api/.env may list a real one).
    monkeypatch.setenv("RECOVA_SUPER_ADMIN_EMAILS", "")
    # No background loop: tests call the daily run directly.
    monkeypatch.setenv("RECOVA_SCHEDULER", "off")
    from app import mailer
    mailer.DEV_SENT.clear()
    engine = db_module.make_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db_module.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def get_test_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = get_test_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def register(client: TestClient, email: str, business: str, name: str = "Owner") -> dict:
    res = client.post("/auth/register", json={"name": name, "email": email,
                                              "password": "correct horse 9",
                                              "business_name": business})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.fixture()
def second_client(client):
    """A separate browser (its own cookie jar) against the same app + database."""
    with TestClient(app) as c:
        yield c
