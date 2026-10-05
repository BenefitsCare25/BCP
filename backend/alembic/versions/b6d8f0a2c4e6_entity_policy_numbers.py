"""Add explicit product/entity policy-number assignments without guessing legacy data.

Revision ID: b6d8f0a2c4e6
Revises: a5c7e9b1d3f6
"""

import sqlalchemy as sa
from alembic import op

revision = "b6d8f0a2c4e6"
down_revision = "a5c7e9b1d3f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("product_terms", sa.Column("policy_number_mappings", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("product_terms", "policy_number_mappings")
