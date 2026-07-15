"""Add product_terms table for per-product coverage periods

Each row overrides one product's coverage window within a policy year. Storage
is sparse — products without a row inherit the policy year's span — so no
backfill is required and existing single-period setups keep working.

Revision ID: a9f3c1e7b204
Revises: c8d9e0f12345
Create Date: 2026-06-02 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9f3c1e7b204"
down_revision: Union[str, None] = "c8d9e0f12345"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_terms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "product_id",
            sa.String(36),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("coverage_start", sa.Date(), nullable=False),
        sa.Column("coverage_end", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.UniqueConstraint("policy_year_id", "product_id", name="uq_product_term_year_product"),
    )


def downgrade() -> None:
    op.drop_table("product_terms")
