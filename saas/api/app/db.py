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

    bind = bind or engine
    Base.metadata.create_all(bind=bind)
    add_missing_columns(bind)


def add_missing_columns(bind) -> list[str]:
    """Add columns a model gained since its table was created -- additive only
    (never drops or alters), so an existing local database keeps its accounts.
    New columns must be nullable or carry a server_default. A stopgap until
    Alembic migrations arrive with Postgres."""
    from sqlalchemy import inspect
    from sqlalchemy.schema import CreateColumn

    added: list[str] = []
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    with bind.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl = CreateColumn(column).compile(dialect=bind.dialect)
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
                added.append(f"{table.name}.{column.name}")
    return added
