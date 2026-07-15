"""Enrollment window flex price-tag source + drawdown rule

Adds two config columns to ``enrollment_windows`` set at enrollment-period
creation:

- ``flex_price_source`` (JSON) — per-product source of the flex price tag:
  ``{product_id: "slip" | "manual"}``. "slip" prices the tag off the placement
  slip's premium; "manual" uses the portal FlexPricing matrix. NULL/missing → manual.
- ``flex_drawdown_rule`` (String) — company-wide rule: "full" deducts the whole
  plan price tag; "on_change" deducts only the upgrade/downgrade difference vs the
  member's default plan. Defaults to "full" (prior behavior).

Both additive; defaults reproduce the existing behavior for windows created before
this change.

Revision ID: e9a1c3d5f7b2
Revises: d8f0b2c4e6a1
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "e9a1c3d5f7b2"
down_revision: Union[str, None] = "d8f0b2c4e6a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrollment_windows",
        sa.Column("flex_price_source", json_variant(), nullable=True),
    )
    op.add_column(
        "enrollment_windows",
        sa.Column(
            "flex_drawdown_rule",
            sa.String(16),
            nullable=False,
            server_default="full",
        ),
    )


def downgrade() -> None:
    op.drop_column("enrollment_windows", "flex_drawdown_rule")
    op.drop_column("enrollment_windows", "flex_price_source")
