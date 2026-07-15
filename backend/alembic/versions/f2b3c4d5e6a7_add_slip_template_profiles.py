"""Add slip_template_profiles table for broker-corrected SOB column mappings

Per-tenant memory of a placement-slip template's column->role layout, keyed by a
stable fingerprint, so a broker's one-time correction is reused on later uploads
of the same carrier template.

Revision ID: f2b3c4d5e6a7
Revises: e7f8a9b01234
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "e7f8a9b01234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "slip_template_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("insurer", sa.String(128), nullable=True),
        sa.Column("sheet_label", sa.String(255), nullable=True),
        sa.Column("roles", json_variant(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "client_id", "fingerprint", name="uq_slip_template_profile_client_fingerprint"
        ),
    )
    op.create_index(
        "ix_slip_template_profiles_client_id", "slip_template_profiles", ["client_id"]
    )
    op.create_index(
        "ix_slip_template_profiles_fingerprint", "slip_template_profiles", ["fingerprint"]
    )


def downgrade() -> None:
    op.drop_index("ix_slip_template_profiles_fingerprint", table_name="slip_template_profiles")
    op.drop_index("ix_slip_template_profiles_client_id", table_name="slip_template_profiles")
    op.drop_table("slip_template_profiles")
