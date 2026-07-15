"""Add flex_schemes table for Flexible-Benefits configuration

One scheme per policy year holds the normalized Flex configuration (meta, the
eligibility tiers, dependant definition) as a JSON bag. Draft → confirmed
lifecycle; confirm does not materialize catalog rows (that is a later phase).

Revision ID: b1c2d3e4f5a6
Revises: a9f3c1e7b204
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a9f3c1e7b204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flex_schemes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("scheme", json_variant(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(512), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("policy_year_id", name="uq_flex_scheme_year"),
    )
    op.create_index("ix_flex_schemes_policy_year_id", "flex_schemes", ["policy_year_id"])
    op.create_index("ix_flex_schemes_status", "flex_schemes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_flex_schemes_status", table_name="flex_schemes")
    op.drop_index("ix_flex_schemes_policy_year_id", table_name="flex_schemes")
    op.drop_table("flex_schemes")
