"""Add plans table for Schedule of Benefits data (Layer 3)

Revision ID: e6f7a8901234
Revises: d5e6f7a89012
Create Date: 2026-05-25 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "e6f7a8901234"
down_revision: Union[str, None] = "d5e6f7a89012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "product_id",
            sa.String(36),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("benefit_schedule", json_variant(), nullable=True),
        sa.Column("cover_description", sa.String(512), nullable=True),
        sa.Column("annual_policy_limit", sa.String(128), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="system_generated"),
        sa.Column("source_ref", sa.String(512), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="needs_review", index=True),
        sa.Column("human_modified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("modified_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("product_id", "policy_year_id", "code", name="uq_plan_product_year_code"),
    )


def downgrade() -> None:
    op.drop_table("plans")
