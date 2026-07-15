"""Leave pricing — per-day buy/sell rate by employee attribute + election snapshot

Adds the ``leave_rates`` JSON bag to ``leave_policies`` (per-day buy/sell leave
rate keyed by an employee grade/designation attribute, NOT age or product) and a
signed ``flex_amount`` snapshot column to ``leave_elections`` (the wallet impact
of the trade, set at election time so reporting is stable if the rates change).
Both additive + back-compatible: existing policies get an empty bag (leave priced
at 0, the prior days-only behavior).

Revision ID: d8f0b2c4e6a1
Revises: c7e9a1b3d5f2
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "d8f0b2c4e6a1"
down_revision: Union[str, None] = "c7e9a1b3d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "leave_policies",
        sa.Column(
            "leave_rates", json_variant(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "leave_elections",
        sa.Column("flex_amount", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("leave_elections", "flex_amount")
    op.drop_column("leave_policies", "leave_rates")
