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
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "inspro.db"
DATABASE_URL = os.environ.get("INSPRO_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

def _pool_setting(name: str, default: int) -> int:
    """Read a positive int pool setting, ignoring junk rather than failing boot."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r is not an integer — using default %s", name, raw, default
        )
        return default
    if value < 0:
        logging.getLogger(__name__).warning(
            "%s=%s is negative — using default %s", name, value, default
        )
        return default
    return value


_engine_kwargs: dict[str, Any] = {"pool_pre_ping": True, "echo": False}
if _IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Sized PER PROCESS — the real ceiling is
    #   workers (WEB_CONCURRENCY) x (pool_size + max_overflow) x instances
    # and it must stay under the server's max_connections. The old 10+20
    # default meant 120 connections from a single 4-worker instance, which
    # overruns every small Postgres SKU. Defaults below give 2 x 5 = 10.
    # Raise together with WEB_CONCURRENCY and the DB tier.
    _engine_kwargs.update(
        pool_size=_pool_setting("INSPRO_DB_POOL_SIZE", 3),
        max_overflow=_pool_setting("INSPRO_DB_MAX_OVERFLOW", 2),
        # Fail a starved request instead of hanging the worker indefinitely.
        pool_timeout=_pool_setting("INSPRO_DB_POOL_TIMEOUT", 30),
        # Recycle below Azure Postgres' idle cutoff so pooled connections
        # can't be handed out already dead.
        pool_recycle=_pool_setting("INSPRO_DB_POOL_RECYCLE", 1800),
    )

engine = create_engine(DATABASE_URL, **_engine_kwargs)


def _is_sqlite_connection(dbapi_connection) -> bool:
    """Whether THIS connection is SQLite, read off the connection itself.

    Both listeners below are registered on the Engine CLASS, so they fire for
    every engine in the process — not just the app's. Branching on the
    module-level ``_IS_SQLITE`` (which only describes the configured app URL)
    therefore sent ``PRAGMA`` to the Postgres engines the gated schema-isolation
    tests create, making that suite unrunnable.
    """
    return type(dbapi_connection).__module__.split(".")[0] == "sqlite3"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if _is_sqlite_connection(dbapi_connection):
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
    if _is_sqlite_connection(dbapi_connection):
        return
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SET search_path TO public")
        dbapi_connection.commit()
        cursor.close()
    except Exception:
        # Isolation doesn't depend on this (set_search_path runs per request),
        # but a connection that can't reset is unhealthy — evict it from the
        # pool rather than handing it back (possibly still on a firm schema).
        # invalidate() is wrapped: it may itself fail on an already-broken
        # connection, and an exception must not escape the checkin event.
        logging.getLogger(__name__).warning(
            "Failed to reset search_path on pool checkin; invalidating connection",
            exc_info=True,
        )
        try:
            _connection_record.invalidate()
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to invalidate connection on checkin", exc_info=True
            )


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
