"""claim amendment columns

Adds ``claims.revision`` and ``claims.amended_at``, the two columns behind
member- and broker-side claim editing.

``revision`` is the optimistic-concurrency guard the broker's decision endpoint
checks (`ClaimDecisionIn.expected_revision`), so it must be NOT NULL from the
first row: a NULL there would compare unequal to every value a client could send
and 409 every decision on a pre-existing claim. Server-defaulted to 0 so the
backfill is the DDL itself.

``amended_at`` stays NULL on existing rows, which is exactly true of them —
none has been amended.

Additive, so ``scripts/provision_tenants.py`` syncs both into every firm schema
on deploy. Design: ``docs/CLAIM_AMENDMENT_PLAN.md``.

Revision ID: d1a7f3c9e260
Revises: c4e8b1d70a35
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1a7f3c9e260"
down_revision = "c4e8b1d70a35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column(
            "revision", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "claims",
        sa.Column("amended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("claims", "amended_at")
    op.drop_column("claims", "revision")
