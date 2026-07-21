"""Broker-configurable claim document types.

Per-client rows (lazily seeded from the in-code defaults in
``services/claim_doc_types.py``); aliases + key fields drive document
classification at intake and the completeness check in the AI review.

Revision ID: c1e3a5b7d9f2
Revises: b9e1d3f5a7c2
Create Date: 2026-07-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "c1e3a5b7d9f2"
down_revision = "b9e1d3f5a7c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claim_doc_types",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("display", sa.String(128), nullable=False),
        sa.Column("aliases", json_variant(), nullable=True),
        sa.Column("key_fields", json_variant(), nullable=True),
        sa.Column("sector", sa.String(16), nullable=True),
        sa.Column("slot_key", sa.String(64), nullable=True),
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
        sa.UniqueConstraint("client_id", "key", name="uq_claim_doc_type_client_key"),
    )


def downgrade() -> None:
    op.drop_table("claim_doc_types")
