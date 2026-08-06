"""claims.doctor_name — the treating doctor on a pre/post-hospitalisation claim

Revision ID: c1e5a7d40b93
Revises: d8b3f1c60a72
Create Date: 2026-08-06

Nullable with no backfill: existing claims were filed before the field existed
and nothing can infer who the doctor was. Only pre-/post-hospitalisation claims
require it, and that requirement is enforced at intake, not by the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1e5a7d40b93"
down_revision = "d8b3f1c60a72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("doctor_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "doctor_name")
