"""Enrollment window flex-wallet overdraft policy

Adds ``allow_overdraft`` (Boolean) to ``enrollment_windows``: whether a
member's elections may draw more flex than their wallet holds. Off (the
default), submit/confirm reject an overdrawn enrollment with a 409; on, the
negative balance is permitted (e.g. shortfall recovered via payroll).

Additive; the default reproduces the safest behavior (enforced) — windows
created before this change gain enforcement, which is the intended production
posture.

Revision ID: f0b2d4e6a8c3
Revises: e9a1c3d5f7b2
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f0b2d4e6a8c3"
down_revision: Union[str, None] = "e9a1c3d5f7b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrollment_windows",
        sa.Column(
            "allow_overdraft",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("enrollment_windows", "allow_overdraft")
