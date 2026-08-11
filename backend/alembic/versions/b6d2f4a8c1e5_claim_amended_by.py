"""claim amendment actor

Adds ``claims.amended_by`` — ``member`` or ``broker``, whichever surface last
corrected the claim.

The queue's "Amended" chip means one thing: this claim moved UNDER the assessor.
Gated on ``amended_at`` alone it fired on the broker's own correction too, since
all three amendment writers stamp that timestamp. This column is what the chip
actually needs.

NULL on existing rows and that is the honest value: a claim amended before this
column existed has no recorded actor, and the chip treats "unknown" as "not the
member" rather than inventing one.

Additive, so ``scripts/provision_tenants.py`` syncs it into every firm schema on
deploy.

Revision ID: b6d2f4a8c1e5
Revises: d1a7f3c9e260
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d2f4a8c1e5"
down_revision = "d1a7f3c9e260"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("amended_by", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("claims", "amended_by")
