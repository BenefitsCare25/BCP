"""Flex pricing matrix + price-tag snapshot on elections/overrides

Adds the per-policy-year flex "price tag" matrix (``flex_pricing``) — the amount
deducted from a member's flex wallet to offset elected coverage, varying by tier
and per-product age band — plus the resolved price-tag snapshot column on the
election and override (set at confirm). All additive + nullable — back-compatible.

Revision ID: b4d6f8a0c2e5
Revises: a2c4e6f8b1d3
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "b4d6f8a0c2e5"
down_revision: Union[str, None] = "a2c4e6f8b1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flex_pricing",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pricing", json_variant(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("policy_year_id", name="uq_flex_pricing_year"),
    )
    op.create_index(
        "ix_flex_pricing_policy_year_id", "flex_pricing", ["policy_year_id"]
    )
    op.create_index("ix_flex_pricing_client_id", "flex_pricing", ["client_id"])
    op.add_column(
        "enrollment_elections",
        sa.Column("flex_price_tag", sa.Float(), nullable=True),
    )
    op.add_column(
        "employee_plan_overrides",
        sa.Column("flex_price_tag", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employee_plan_overrides", "flex_price_tag")
    op.drop_column("enrollment_elections", "flex_price_tag")
    op.drop_index("ix_flex_pricing_client_id", table_name="flex_pricing")
    op.drop_index("ix_flex_pricing_policy_year_id", table_name="flex_pricing")
    op.drop_table("flex_pricing")
