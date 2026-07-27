"""underwriting_reviews table + case review link + product_terms.nel_age_limit.

Insurer-grouped underwriting: a review is one case per (life, insurer, year)
carrying the workflow status + requirements; underwriting_cases become its
per-product decision lines (review_id/guaranteed_si added). Additive only —
legacy per-product rows keep working (review_id NULL) and are adopted into
reviews by the next sync; legacy accepted/declined statuses are normalized in
code, so firm schemas need no data migration.

Revision ID: d8f0a2c4e6b8
Revises: c7e9a1b3d5f6
Create Date: 2026-07-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f0a2c4e6b8"
down_revision = "c7e9a1b3d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_terms",
        sa.Column("nel_age_limit", sa.Integer(), nullable=True),
    )
    op.create_table(
        "underwriting_reviews",
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
        sa.Column("insurer", sa.String(128), nullable=False, server_default=""),
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
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending_requirements",
        ),
        sa.Column("requirements", sa.String(2000), nullable=True),
        sa.Column("modified_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_uw_reviews_year_status",
        "underwriting_reviews",
        ["policy_year_id", "status"],
    )
    op.add_column(
        "underwriting_cases",
        sa.Column("review_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_uw_cases_review_id", "underwriting_cases", ["review_id"]
    )
    op.add_column(
        "underwriting_cases",
        sa.Column("guaranteed_si", sa.Float(), nullable=True),
    )
    op.add_column(
        "underwriting_cases",
        sa.Column(
            "guaranteed_overridden",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("underwriting_cases", "guaranteed_overridden")
    op.drop_column("underwriting_cases", "guaranteed_si")
    op.drop_index("ix_uw_cases_review_id", table_name="underwriting_cases")
    op.drop_column("underwriting_cases", "review_id")
    op.drop_index("ix_uw_reviews_year_status", table_name="underwriting_reviews")
    op.drop_table("underwriting_reviews")
