"""plans.report_label — insurer-facing plan label for report columns.

Revision ID: d4f6a8b0c2e1
Revises: c3e5a7b9d1f2
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4f6a8b0c2e1"
down_revision = "c3e5a7b9d1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("report_label", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "report_label")
