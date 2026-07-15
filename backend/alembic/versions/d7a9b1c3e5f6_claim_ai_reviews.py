"""Claim AI reviews

Tenant table for the Phase-3 claims AI review pipeline: one row per pipeline
run over a submitted claim (extractions, field comparisons, rule results,
vision checks, verdict). Reruns supersede rather than mutate, so the broker
always sees what the AI said at decision time.

Auto-provisions into firm schemas via ``tenancy.sync_firm_schema``.

Revision ID: d7a9b1c3e5f6
Revises: c6f8b0d2e4a5
Create Date: 2026-07-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "d7a9b1c3e5f6"
down_revision: Union[str, None] = "c6f8b0d2e4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claim_ai_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("verdict", sa.String(16), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("extractions", json_variant(), nullable=True),
        sa.Column("field_comparisons", json_variant(), nullable=True),
        sa.Column("rule_results", json_variant(), nullable=True),
        sa.Column("vision_checks", json_variant(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_claim_ai_reviews_client_id", "claim_ai_reviews", ["client_id"])
    op.create_index("ix_claim_ai_reviews_claim_id", "claim_ai_reviews", ["claim_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_ai_reviews_claim_id", table_name="claim_ai_reviews")
    op.drop_index("ix_claim_ai_reviews_client_id", table_name="claim_ai_reviews")
    op.drop_table("claim_ai_reviews")
