"""Company Employee Listing layout profiles and listed cover per person.

Revision ID: 16b00754757b
Revises: f0b2c4d6e8a0
Create Date: 2026-10-07

Additive: two new tenant tables. ``scripts/provision_tenants.py`` propagates
new tables to every firm schema on deploy. Downgrade drops only these tables.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant

revision = "16b00754757b"
down_revision = "f0b2c4d6e8a0"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "el_layout_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sheet_name", sa.String(255), nullable=False),
        sa.Column("layout", json_variant(), nullable=False),
        sa.Column("block_products", json_variant(), nullable=False),
        sa.Column("label_map", json_variant(), nullable=False),
        sa.Column("source_filename", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("client_id", name="uq_el_layout_profile_client"),
    )
    op.create_index("ix_el_layout_profiles_client_id", "el_layout_profiles", ["client_id"])

    op.create_table(
        "listing_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36),
                  sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_year_id", sa.String(36),
                  sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(36),
                  sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependant_id", sa.String(36),
                  sa.ForeignKey("dependants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("member_key", sa.String(48), nullable=False),
        sa.Column("product_id", sa.String(36),
                  sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category_id", sa.String(36),
                  sa.ForeignKey("categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("block_index", sa.Integer(), nullable=False),
        sa.Column("listed_category", sa.String(512), nullable=True),
        sa.Column("listed_plan", sa.String(255), nullable=True),
        sa.Column("family_group", sa.String(2), nullable=True),
        sa.Column("admin_type", sa.String(16), nullable=True),
        sa.Column("recorded", json_variant(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "policy_year_id", "product_id", "member_key",
            name="uq_listing_assignment_member_product",
        ),
    )
    for column in ("client_id", "policy_year_id", "employee_id", "dependant_id",
                   "product_id", "category_id"):
        op.create_index(
            f"ix_listing_assignments_{column}", "listing_assignments", [column]
        )


def downgrade() -> None:
    op.drop_table("listing_assignments")
    op.drop_table("el_layout_profiles")
