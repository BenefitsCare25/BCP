"""Revocable portal sessions and explicit mandatory MFA policy.

Revision ID: f4a6b8c0d2e4
Revises: e3f5a7c9d1b2
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a6b8c0d2e4"
down_revision: str | None = "e3f5a7c9d1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("auth_sessions", sa.Column(
        "mfa_verified", sa.Boolean(), nullable=False, server_default=sa.false(),
    ))
    op.add_column("auth_sessions", sa.Column(
        "last_seen_at", sa.DateTime(timezone=True), nullable=True,
    ))
    for name in ("mfa_hr_required", "mfa_portal_required"):
        op.add_column("client_auth_policy", sa.Column(
            name, sa.Boolean(), nullable=False, server_default=sa.false(),
        ))


def downgrade() -> None:
    for name in ("mfa_hr_required", "mfa_portal_required"):
        op.drop_column("client_auth_policy", name)
    op.drop_column("auth_sessions", "last_seen_at")
    op.drop_column("auth_sessions", "mfa_verified")
