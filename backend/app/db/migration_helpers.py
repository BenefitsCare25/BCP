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

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def json_variant() -> sa.types.TypeEngine:
    """JSON column that uses Postgres `jsonb` and generic `json` elsewhere."""
    return sa.JSON().with_variant(JSONB(), "postgresql")


@contextlib.contextmanager
def sqlite_fk_guard(bind) -> Iterator[None]:
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
    if is_sqlite:
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        yield
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
