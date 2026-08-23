"""Enforce non-overlapping benefit years and one live year per company.

Revision ID: e1b2c3d4f5a6
Revises: d8a1f3c5e7b9
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1b2c3d4f5a6"
down_revision: str | None = "d8a1f3c5e7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicate_active = bind.execute(
        sa.text(
            """
            SELECT client_id
            FROM policy_years
            WHERE status = 'active'
            GROUP BY client_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate_active is not None:
        raise RuntimeError(
            "Cannot enforce benefit-year lifecycle: a company has multiple active years."
        )

    overlapping = bind.execute(
        sa.text(
            """
            SELECT a.client_id
            FROM policy_years a
            JOIN policy_years b
              ON a.client_id = b.client_id
             AND a.id < b.id
             AND a.start_date <= b.end_date
             AND b.start_date <= a.end_date
            LIMIT 1
            """
        )
    ).first()
    if overlapping is not None:
        raise RuntimeError("Cannot enforce benefit-year dates: overlapping periods already exist.")

    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        op.execute(
            """
            ALTER TABLE policy_years
            ADD CONSTRAINT ex_policy_year_client_dates
            EXCLUDE USING gist (
              client_id WITH =,
              daterange(start_date, end_date, '[]') WITH &&
            )
            """
        )
        op.create_index(
            "uq_policy_year_active_client",
            "policy_years",
            ["client_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )
    elif dialect == "sqlite":
        op.create_index(
            "uq_policy_year_active_client",
            "policy_years",
            ["client_id"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    op.drop_index("uq_policy_year_active_client", table_name="policy_years")
    if dialect == "postgresql":
        op.execute("ALTER TABLE policy_years DROP CONSTRAINT IF EXISTS ex_policy_year_client_dates")
