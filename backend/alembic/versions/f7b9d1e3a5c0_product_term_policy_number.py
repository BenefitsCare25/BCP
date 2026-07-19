"""product_terms.policy_number.

Revision ID: f7b9d1e3a5c0
Revises: e5a7b9c1d3f2
Create Date: 2026-07-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7b9d1e3a5c0"
down_revision = "e5a7b9c1d3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_terms",
        sa.Column("policy_number", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("product_terms", "policy_number")
