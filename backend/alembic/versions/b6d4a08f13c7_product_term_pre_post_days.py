"""product_terms.pre_hosp_days / post_hosp_days — the pre/post claim window

Revision ID: b6d4a08f13c7
Revises: a3f7d21c9e84
Create Date: 2026-08-12

How long before an admission and after a discharge a consultation is still
claimable against it. Nullable with no backfill and no default: NULL means "no
rule", which is what every existing product means today — a zero default would
turn the rule on for all of them and flag every pre-/post-hospitalisation claim
in the system.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d4a08f13c7"
down_revision = "a3f7d21c9e84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_terms", sa.Column("pre_hosp_days", sa.Integer(), nullable=True)
    )
    op.add_column(
        "product_terms", sa.Column("post_hosp_days", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("product_terms", "post_hosp_days")
    op.drop_column("product_terms", "pre_hosp_days")
