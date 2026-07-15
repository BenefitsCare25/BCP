"""SQLAlchemy engine + session factory.

SQLite is the default for local dev; Postgres for production. Switch via
`INSPRO_DATABASE_URL` (e.g. `postgresql+psycopg://user:pw@host:5432/inspro`).
Pool sizing kicks in only for non-SQLite dialects.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "inspro.db"
DATABASE_URL = os.environ.get("INSPRO_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {"pool_pre_ping": True, "echo": False}
if _IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_size=10, max_overflow=20)

engine = create_engine(DATABASE_URL, **_engine_kwargs)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if _IS_SQLITE:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


@event.listens_for(Engine, "checkin")
def _reset_search_path(dbapi_connection, _connection_record) -> None:
    """Reset the per-tenant search_path when a Postgres connection returns to
    the pool, so a later request can never inherit a previous request's firm
    schema. Tenant routing (`set_search_path`) re-establishes it per request;
    this is the belt-and-braces reset. No-op on SQLite."""
    if _IS_SQLITE:
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET search_path TO public")
        dbapi_connection.commit()
        cursor.close()
    except Exception:
        # Isolation doesn't depend on this (set_search_path runs per request),
        # but a connection failing to reset is a sign it's unhealthy — surface
        # it instead of silently returning it to the pool.
        logging.getLogger(__name__).warning(
            "Failed to reset search_path on pool checkin", exc_info=True
        )


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
