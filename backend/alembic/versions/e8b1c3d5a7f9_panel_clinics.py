"""Panel clinic locator tables

Tenant tables for the clinic locator: `panel_listings` (one uploaded network
list per insurer/provider/country/clinic-type), `panel_clinics` (its clinics,
replaced wholesale per upload) and `policy_year_panels` (tags a listing to a
policy year — the member-visibility switch).

Auto-provisions into firm schemas via ``tenancy.sync_firm_schema``.

Revision ID: e8b1c3d5a7f9
Revises: d7a9b1c3e5f6
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "e8b1c3d5a7f9"
down_revision: Union[str, None] = "d7a9b1c3e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_listings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("insurer", sa.String(64), nullable=False),
        sa.Column("panel_provider", sa.String(64), nullable=False),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("clinic_type", sa.String(16), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("source_filename", sa.String(255), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "client_id",
            "insurer",
            "panel_provider",
            "country",
            "clinic_type",
            name="uq_panel_listing_combo",
        ),
    )
    op.create_index("ix_panel_listings_client_id", "panel_listings", ["client_id"])

    op.create_table(
        "panel_clinics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "panel_listing_id",
            sa.String(36),
            sa.ForeignKey("panel_listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("zone", sa.String(64), nullable=True),
        sa.Column("area", sa.String(64), nullable=True),
        sa.Column("specialty", sa.String(128), nullable=True),
        sa.Column("doctor", sa.String(255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.String(16), nullable=True),
        sa.Column("country", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(128), nullable=True),
        sa.Column("hours", json_variant(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("google_map_url", sa.String(512), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_panel_clinics_panel_listing_id", "panel_clinics", ["panel_listing_id"]
    )

    op.create_table(
        "policy_year_panels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "panel_listing_id",
            sa.String(36),
            sa.ForeignKey("panel_listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "policy_year_id", "panel_listing_id", name="uq_policy_year_panel"
        ),
    )
    op.create_index(
        "ix_policy_year_panels_policy_year_id", "policy_year_panels", ["policy_year_id"]
    )
    op.create_index(
        "ix_policy_year_panels_panel_listing_id",
        "policy_year_panels",
        ["panel_listing_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_policy_year_panels_panel_listing_id", table_name="policy_year_panels"
    )
    op.drop_index(
        "ix_policy_year_panels_policy_year_id", table_name="policy_year_panels"
    )
    op.drop_table("policy_year_panels")
    op.drop_index("ix_panel_clinics_panel_listing_id", table_name="panel_clinics")
    op.drop_table("panel_clinics")
    op.drop_index("ix_panel_listings_client_id", table_name="panel_listings")
    op.drop_table("panel_listings")
