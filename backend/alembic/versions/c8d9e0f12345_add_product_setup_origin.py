"""add origin + origin_ref to product_setups

Revision ID: c8d9e0f12345
Revises: b7c8d9e0f123
Create Date: 2026-05-29

Tracks how a setup draft's answers were produced so confirm can supersede the
provisional system_generated rows when the draft was pre-filled from a slip.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d9e0f12345"
down_revision = "b7c8d9e0f123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_setups",
        sa.Column(
            "origin",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "product_setups",
        sa.Column("origin_ref", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_setups", "origin_ref")
    op.drop_column("product_setups", "origin")
