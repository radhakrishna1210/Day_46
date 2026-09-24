"""Database engine and session.

SQLite for local development (a file under saas/api/), Postgres in production:
the only difference is DATABASE_URL. Every table carries tenant_id, and every
query the API runs is scoped to the caller's tenant (see app/deps.py) -- in
Postgres, row-level security can be layered on top as a second barrier.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = f"sqlite:///{(API_ROOT / 'recova.db').as_posix()}"


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    url = url or os.environ.get("DATABASE_URL", DEFAULT_URL)
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):   # SQLite ignores foreign keys unless asked
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_all(bind=None) -> None:
    from app import models  # noqa: F401  -- registers the tables

    Base.metadata.create_all(bind=bind or engine)
