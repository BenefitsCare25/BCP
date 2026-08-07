"""enrollment_windows.member_self_service (hide an open period from the portal)

Revision ID: d5b8e1a4c7f2
Revises: a3f7c9d21b48
Create Date: 2026-08-07

Purely additive: one NOT NULL boolean with a server default, so
`scripts/provision_tenants.py` propagates it to every firm schema on deploy and
no per-schema ALTER is needed here (contrast `a3f7c9d21b48`, which had to drop a
NOT NULL and therefore had to walk the schemas itself).

The default is TRUE — every existing period keeps behaving exactly as it does
today, member-visible. A false default would silently take the portal's
enrolment surface dark for anyone mid-period at deploy time.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5b8e1a4c7f2"
down_revision = "a3f7c9d21b48"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enrollment_windows",
        sa.Column(
            "member_self_service",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("enrollment_windows", "member_self_service")
