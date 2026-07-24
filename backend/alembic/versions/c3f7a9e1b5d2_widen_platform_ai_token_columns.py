"""widen platform_ai_settings token columns to BigInteger

``platform_monthly_token_cap`` and ``default_monthly_token_budget`` are token
counts that can exceed int32: a fleet cap of a few billion tokens is realistic
and the API validator (``PlatformAISettingsUpdate``) accepts up to 1e12. They
were created as ``Integer``, which overflows on Postgres
(``NumericValueOutOfRange``, int32 max 2,147,483,647) while SQLite's 64-bit
INTEGER hides the mismatch. Widen both to ``BigInteger`` (matches
``platform_ai_usage.total_tokens``). ``max_concurrent_calls`` stays Integer.

No-op on SQLite (INTEGER is already 64-bit). ``platform_ai_settings`` is a
CONTROL table in ``public``, so no per-firm-schema iteration is needed.

Revision ID: c3f7a9e1b5d2
Revises: a1c3e5f7b9d0
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f7a9e1b5d2"
down_revision = "a1c3e5f7b9d0"
branch_labels = None
depends_on = None

_COLUMNS = ("platform_monthly_token_cap", "default_monthly_token_budget")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite INTEGER is already 64-bit — nothing to widen.
    for col in _COLUMNS:
        op.alter_column(
            "platform_ai_settings",
            col,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for col in _COLUMNS:
        op.alter_column(
            "platform_ai_settings",
            col,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
