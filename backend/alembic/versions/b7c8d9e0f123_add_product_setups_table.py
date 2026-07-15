"""Add product_setups table for guided product-setup drafts

Revision ID: b7c8d9e0f123
Revises: a1b2c3d4e5f6
Create Date: 2026-05-26 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "b7c8d9e0f123"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_setups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("template_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("answers", json_variant(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft", index=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(36), nullable=True),
        sa.Column("materialized_product_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("policy_year_id", "product_code", name="uq_product_setup_year_code"),
    )


def downgrade() -> None:
    op.drop_table("product_setups")
