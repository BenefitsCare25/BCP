"""Declare whether an enrollment period uses Flex wallets.

Revision ID: a3d4e5f6b7c8
Revises: f2c3d4e5a6b7
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3d4e5f6b7c8"
down_revision: str | None = "f2c3d4e5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enrollment_windows",
        sa.Column(
            "uses_flex",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Preserve periods that demonstrably used Flex before this explicit flag
    # existed. A wallet or price-book row alone is not enough (both can be
    # stale/draft data), but a saved coverage or leave amount is a durable
    # transaction snapshot and must retain its historical behavior.
    op.execute(
        sa.text(
            """
            UPDATE enrollment_windows
               SET uses_flex = true
             WHERE id IN (
                 SELECT DISTINCT enrollment.window_id
                   FROM enrollments AS enrollment
                   JOIN enrollment_elections AS election
                     ON election.enrollment_id = enrollment.id
                  WHERE election.flex_price_tag IS NOT NULL
                 UNION
                 SELECT DISTINCT enrollment.window_id
                   FROM enrollments AS enrollment
                   JOIN leave_elections AS leave_election
                     ON leave_election.enrollment_id = enrollment.id
                  WHERE leave_election.flex_amount IS NOT NULL
             )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("enrollment_windows", "uses_flex")
