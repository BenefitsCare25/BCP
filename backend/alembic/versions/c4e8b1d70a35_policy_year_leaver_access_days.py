"""policy year leaver access window

Adds ``policy_years.leaver_access_days`` — how long after a member's last day of
service they keep portal access. NULL means the system default
(``member_access.DEFAULT_LEAVER_ACCESS_DAYS``), which is true of every existing
row, so nothing is backfilled.

Additive, so ``scripts/provision_tenants.py`` syncs it into every firm schema on
deploy. Design: ``docs/LEAVER_ACCESS_PLAN.md``.

Revision ID: c4e8b1d70a35
Revises: b6d3f8a1c052
Create Date: 2026-08-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e8b1d70a35"
down_revision = "b6d3f8a1c052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_years",
        sa.Column("leaver_access_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("policy_years", "leaver_access_days")
