"""Add Flex-assignment columns to employees

Persists each employee's resolved Flexible-Benefits wallet (family status, tier,
amount, currency) so the benefit statement, activation and reporting can read a
stable Flex entitlement instead of recomputing it per request.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("flex_family_status", sa.String(8), nullable=True))
    op.add_column("employees", sa.Column("flex_tier_name", sa.String(255), nullable=True))
    op.add_column("employees", sa.Column("flex_wallet_amount", sa.Float(), nullable=True))
    op.add_column("employees", sa.Column("flex_currency", sa.String(8), nullable=True))
    op.add_column("employees", sa.Column("flex_source", sa.String(16), nullable=True))
    op.add_column(
        "employees",
        sa.Column("flex_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employees", "flex_assigned_at")
    op.drop_column("employees", "flex_source")
    op.drop_column("employees", "flex_currency")
    op.drop_column("employees", "flex_wallet_amount")
    op.drop_column("employees", "flex_tier_name")
    op.drop_column("employees", "flex_family_status")
