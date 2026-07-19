"""Panel clinic e-cards.

`panel_cards` = shared library of card ARTWORK + field placements (keyed by
insurer/provider/name, `client_id` NULL for library entries).
`policy_year_cards` = per-company, per-benefit-year assignment carrying the
printed data (product, member-ID sources, service badges, remarks).

Auto-provisions into firm schemas via ``tenancy.sync_firm_schema``.

Revision ID: a7c9e1b3d5f0
Revises: b2d4f6a8c0e1
Create Date: 2026-07-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "a7c9e1b3d5f0"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "panel_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("insurer", sa.String(64), nullable=False),
        sa.Column("panel_provider", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("artwork_front_path", sa.String(512), nullable=True),
        sa.Column("artwork_front_mime", sa.String(64), nullable=True),
        sa.Column("artwork_back_path", sa.String(512), nullable=True),
        sa.Column("artwork_back_mime", sa.String(64), nullable=True),
        sa.Column("aspect_ratio", sa.Float(), nullable=True),
        sa.Column("placements", json_variant(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "client_id", "insurer", "panel_provider", "name", name="uq_panel_card_combo"
        ),
    )

    op.create_table(
        "policy_year_cards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "panel_card_id",
            sa.String(36),
            sa.ForeignKey("panel_cards.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "product_id",
            sa.String(36),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "employee_member_id_source",
            sa.String(32),
            nullable=False,
            server_default="insurer_member_id",
        ),
        sa.Column(
            "dependant_member_id_source",
            sa.String(32),
            nullable=False,
            server_default="insurer_member_id",
        ),
        sa.Column("services", json_variant(), nullable=True),
        sa.Column("remarks", json_variant(), nullable=True),
        sa.Column("special_conditions", sa.Text(), nullable=True),
        sa.Column(
            "show_future_cards",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "policy_year_id", "product_id", name="uq_policy_year_card_product"
        ),
    )


def downgrade() -> None:
    op.drop_table("policy_year_cards")
    op.drop_table("panel_cards")
