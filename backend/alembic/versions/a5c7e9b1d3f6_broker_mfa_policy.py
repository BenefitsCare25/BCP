"""Administrator-managed broker authenticator requirement, off by default.

Revision ID: a5c7e9b1d3f6
Revises: f4a6b8c0d2e4
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a5c7e9b1d3f6"
down_revision: str | None = "f4a6b8c0d2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "broker_mfa_required", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))


def downgrade() -> None:
    op.drop_column("users", "broker_mfa_required")
