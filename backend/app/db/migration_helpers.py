"""Cross-dialect helpers for Alembic migrations.

SQLite and Postgres differ on:
- JSON columns: Postgres has both `json` and `jsonb`; we want `jsonb` for GIN
  indexing and `@>` containment query support.
- Enum/string alters: Postgres ENUM types require `USING <expr>` for type
  changes; SQLite uses batch table rebuilds.

Use `json_variant()` for JSON columns so a single migration runs cleanly on
both backends. Lives under `app.db` (not under `alembic/`) because `alembic/`
is also a third-party package name that shadows local modules at import time.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection


def json_variant() -> sa.types.TypeEngine[Any]:
    """JSON column that uses Postgres `jsonb` and generic `json` elsewhere."""
    return sa.JSON().with_variant(JSONB(), "postgresql")


@contextlib.contextmanager
def sqlite_fk_guard(bind: Connection) -> Iterator[None]:
    """Disable SQLite FK enforcement for the duration of a batch table-recreate.

    The app engine sets ``PRAGMA foreign_keys=ON`` on every connection, so any
    ``batch_alter_table`` that isn't a bare ``add_column`` (constraint changes,
    ``alter_column`` non-nullable, etc.) recreates the table via CREATE-copy-
    DROP-rename, and the DROP fires every ``ON DELETE CASCADE``/``SET NULL`` FK
    pointing at it — silently wiping/nulling child rows. Postgres does an
    in-place ALTER and never hits this, so the guard is a SQLite-only no-op
    elsewhere.

    Any migration doing a batch recreate MUST wrap it::

        with sqlite_fk_guard(op.get_bind()):
            with op.batch_alter_table("foo") as batch:
                batch.create_unique_constraint(...)
    """
    is_sqlite = bind.dialect.name == "sqlite"
    was_enabled = bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar()) if is_sqlite else False
    if was_enabled:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        yield
    finally:
        if was_enabled:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")


@contextlib.contextmanager
def sqlite_migration_guard(bind: Connection) -> Iterator[None]:
    """Protect an entire SQLite Alembic run from batch-recreate cascades.

    Individual migrations use :func:`sqlite_fk_guard`, but a missed wrapper on
    one historical migration can still drop every child row that references the
    recreated table. The Alembic environment wraps the full run with this
    safety net, validates all foreign keys afterwards, then restores the
    connection's original enforcement state. Nested ``sqlite_fk_guard`` calls
    preserve the disabled state rather than turning enforcement on mid-run.
    """
    if bind.dialect.name != "sqlite":
        yield
        return

    was_enabled = bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar())
    if was_enabled:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        yield
        violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if violations:
            sample = violations[:5]
            raise RuntimeError(
                "SQLite migration left foreign-key violations "
                f"({len(violations)} total; first rows: {sample!r})."
            )
        # Alembic treats SQLite DDL as non-transactional, but data migrations
        # and the revision marker still share this SQLAlchemy transaction.
        # Persist them only after the integrity check succeeds.
        bind.commit()
    except BaseException:
        # In particular, keep a failed integrity check from persisting either
        # its violating DML or the Alembic revision that would prevent retry.
        bind.rollback()
        raise
    finally:
        if was_enabled:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
