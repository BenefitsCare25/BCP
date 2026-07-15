"""Elected dependant option level per role

Adds ``dependant_option_ids`` (JSON ``{role: category_id}``) to
``enrollment_elections`` and ``employee_plan_overrides``: the freestanding
dependant option LEVEL the member elected per role (spouse/child) when the
slip lists multiple unlinked levels (e.g. GTL Spouse S$20k/40k/60k). Linked
option rows (GPA markers, VDL composition) price without an election, so the
column stays NULL for them. Additive; NULL preserves existing behavior.

Revision ID: a3c5e7f9b1d2
Revises: f0b2d4e6a8c3
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, None] = "f0b2d4e6a8c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "enrollment_elections",
        sa.Column("dependant_option_ids", json_variant(), nullable=True),
    )
    op.add_column(
        "employee_plan_overrides",
        sa.Column("dependant_option_ids", json_variant(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employee_plan_overrides", "dependant_option_ids")
    op.drop_column("enrollment_elections", "dependant_option_ids")
