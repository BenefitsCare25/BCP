"""PolicyYear: add start_date / end_date so policies can run off the calendar year.

Backfills existing rows by treating `year` as a calendar year
(start = Jan 1, end = Dec 31). Drops the (client_id, year) unique constraint
since two policies may now share a calendar year if they start on different
months; adds a (client_id, start_date) unique constraint instead.

`year` is preserved as a derived label column for snapshot compatibility.

Revision ID: b2f1c5a7d402
Revises: a8c1d4e2b001
Create Date: 2026-05-14 09:00:00.000000
"""
from datetime import date
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2f1c5a7d402"
down_revision: Union[str, None] = "a8c1d4e2b001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("policy_years", schema=None) as batch_op:
        batch_op.add_column(sa.Column("start_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("end_date", sa.Date(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, year FROM policy_years")).fetchall()
    for row in rows:
        py_id, year = row[0], int(row[1])
        bind.execute(
            sa.text(
                "UPDATE policy_years SET start_date = :s, end_date = :e WHERE id = :id"
            ),
            {
                "id": py_id,
                "s": date(year, 1, 1).isoformat(),
                "e": date(year, 12, 31).isoformat(),
            },
        )

    with op.batch_alter_table("policy_years", schema=None) as batch_op:
        batch_op.alter_column("start_date", existing_type=sa.Date(), nullable=False)
        batch_op.alter_column("end_date", existing_type=sa.Date(), nullable=False)
        batch_op.drop_constraint("uq_policy_year", type_="unique")
        batch_op.create_unique_constraint(
            "uq_policy_year_start", ["client_id", "start_date"]
        )


def downgrade() -> None:
    with op.batch_alter_table("policy_years", schema=None) as batch_op:
        batch_op.drop_constraint("uq_policy_year_start", type_="unique")
        batch_op.create_unique_constraint("uq_policy_year", ["client_id", "year"])
        batch_op.drop_column("end_date")
        batch_op.drop_column("start_date")
