"""Per-claim-type AI review rule setup.

One row per (client, claim type) carrying the configurable review dimensions
(field maps, severity-graded AI rules, required documents); absence of a row
keeps the in-code defaults. Also stamps review provenance onto
``claim_ai_reviews`` (which config drove a run — no FK, must survive the
config row's deletion).

Revision ID: e6a8c0b2d4f6
Revises: d8f0a2c4e6b8
Create Date: 2026-07-31
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "e6a8c0b2d4f6"
down_revision = "d8f0a2c4e6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claim_review_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("claim_kind", sa.String(16), nullable=False),
        sa.Column("claim_key", sa.String(128), nullable=False),
        sa.Column("display_label", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("field_maps", json_variant(), nullable=True),
        sa.Column("ai_rules", json_variant(), nullable=True),
        sa.Column("required_documents", json_variant(), nullable=True),
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
            "client_id", "claim_kind", "claim_key",
            name="uq_claim_review_config_client_type",
        ),
    )
    op.add_column(
        "claim_ai_reviews",
        sa.Column("review_config_id", sa.String(36), nullable=True),
    )
    op.add_column(
        "claim_ai_reviews",
        sa.Column("review_config_label", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("claim_ai_reviews", "review_config_label")
    op.drop_column("claim_ai_reviews", "review_config_id")
    op.drop_table("claim_review_configs")
