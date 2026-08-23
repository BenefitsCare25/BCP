"""Add the Medical / General product underwriting setting.

Revision ID: d8a1f3c5e7b9
Revises: d7f9b1c3e5a8
Create Date: 2026-08-23
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d8a1f3c5e7b9"
down_revision = "d7f9b1c3e5a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_terms",
        sa.Column(
            "underwriting_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("product_terms", "underwriting_required")
