"""Participation detail on categories + elected tier on elections/overrides

Adds the structured participation reading parsed from the slip Participation
cell (employee/dependant modes + voluntary change direction) and the elected
cohort-tier reference used to distinguish tiers that share a plan_code
(e.g. GPA "Option N"). All additive + nullable — back-compatible.

Revision ID: a2c4e6f8b1d3
Revises: f2b3c4d5e6a7
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "a2c4e6f8b1d3"
down_revision: Union[str, None] = "f2b3c4d5e6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("participation_detail", json_variant(), nullable=True),
    )
    op.add_column(
        "enrollment_elections",
        sa.Column("tier_category_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "employee_plan_overrides",
        sa.Column("tier_category_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("employee_plan_overrides", "tier_category_id")
    op.drop_column("enrollment_elections", "tier_category_id")
    op.drop_column("categories", "participation_detail")
