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

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def json_variant() -> sa.types.TypeEngine:
    """JSON column that uses Postgres `jsonb` and generic `json` elsewhere."""
    return sa.JSON().with_variant(JSONB(), "postgresql")
