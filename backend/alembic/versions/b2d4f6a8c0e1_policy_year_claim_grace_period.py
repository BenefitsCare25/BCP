"""policy_years.claim_grace_period_days.

Revision ID: b2d4f6a8c0e1
Revises: f7b9d1e3a5c0
Create Date: 2026-07-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2d4f6a8c0e1"
down_revision = "f7b9d1e3a5c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_years",
        sa.Column("claim_grace_period_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("policy_years", "claim_grace_period_days")
