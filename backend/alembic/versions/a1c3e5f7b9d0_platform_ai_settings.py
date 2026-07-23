"""Platform-wide AI limits (single global row).

Shared-key spend controls: a fleet-wide token cap, a default per-tenant budget,
and a concurrency limit — all global because every tenant shares one Vertex
key/quota. Control table (public); see ``services/platform_ai_settings.py``.

Revision ID: a1c3e5f7b9d0
Revises: f2a4c6e8b0d3
Create Date: 2026-07-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c3e5f7b9d0"
down_revision = "f2a4c6e8b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_ai_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("platform_monthly_token_cap", sa.Integer(), nullable=True),
        sa.Column("default_monthly_token_budget", sa.Integer(), nullable=True),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Cross-firm shared-quota usage counter (one row per UTC month). Must live in
    # public so the platform cap aggregates across firm schemas on Postgres.
    op.create_table(
        "platform_ai_usage",
        sa.Column("year_month", sa.String(7), primary_key=True),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_ai_usage")
    op.drop_table("platform_ai_settings")
