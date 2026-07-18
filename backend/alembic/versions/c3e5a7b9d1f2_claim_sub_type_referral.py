"""claims: sub_type + referral_document_id (smart claim intake)

Revision ID: c3e5a7b9d1f2
Revises: b5f0c1a2d3e4
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3e5a7b9d1f2"
down_revision = "b5f0c1a2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("sub_type", sa.String(64), nullable=True))
    op.add_column(
        "claims", sa.Column("referral_document_id", sa.String(36), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("claims", "referral_document_id")
    op.drop_column("claims", "sub_type")
