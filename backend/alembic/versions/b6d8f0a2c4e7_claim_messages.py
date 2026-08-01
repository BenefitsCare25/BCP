"""Claim conversation thread (member <-> broker + system notices).

One row per message on a claim. Read state is two nullable columns (member /
broker) rather than one flag — see models/claim_message.py for why.

Revision ID: b6d8f0a2c4e7
Revises: e6a8c0b2d4f6
Create Date: 2026-08-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6d8f0a2c4e7"
down_revision = "e6a8c0b2d4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claim_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("author_type", sa.String(16), nullable=False),
        sa.Column("author_user_id", sa.String(36), nullable=True),
        sa.Column("author_member_id", sa.String(36), nullable=True),
        sa.Column("author_name", sa.String(255), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("event", sa.String(32), nullable=True),
        sa.Column("member_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("broker_read_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_claim_messages_claim_created",
        "claim_messages",
        ["claim_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_claim_messages_claim_created", table_name="claim_messages")
    op.drop_table("claim_messages")
