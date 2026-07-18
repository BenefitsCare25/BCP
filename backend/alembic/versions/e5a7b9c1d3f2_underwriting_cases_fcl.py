"""underwriting_cases table + product_terms.free_cover_limit.

Revision ID: e5a7b9c1d3f2
Revises: d4f6a8b0c2e1
Create Date: 2026-07-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a7b9c1d3f2"
down_revision = "d4f6a8b0c2e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_terms",
        sa.Column("free_cover_limit", sa.Float(), nullable=True),
    )
    op.create_table(
        "underwriting_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
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
            "employee_id",
            sa.String(36),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "dependant_id",
            sa.String(36),
            sa.ForeignKey("dependants.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("eligible_si", sa.Float(), nullable=False, server_default="0"),
        sa.Column("accepted_si", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending"
        ),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column("remarks", sa.String(1024), nullable=True),
        sa.Column("modified_by", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_uw_cases_year_status", "underwriting_cases", ["policy_year_id", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_uw_cases_year_status", table_name="underwriting_cases")
    op.drop_table("underwriting_cases")
    op.drop_column("product_terms", "free_cover_limit")
