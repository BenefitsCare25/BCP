"""employee flex proration derivation

Adds ``employees.flex_proration`` — the derivation behind a pro-rated flex
allowance (basis / factor / served / total / full_amount / period). NULL means no
pro-ration was applied, so the stored wallet IS the annual allowance, which is
true of every existing row.

**Rewrites nothing.** Existing wallets keep their values until the next flex
assignment run, matching how every other re-projection in this codebase behaves.

Revision ID: b6d3f8a1c052
Revises: a3f7c2d9e614
Create Date: 2026-08-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "b6d3f8a1c052"
down_revision = "a3f7c2d9e614"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employees",
        sa.Column("flex_proration", json_variant(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "flex_proration")
