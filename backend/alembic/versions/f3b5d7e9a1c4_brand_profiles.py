"""White-label brand profiles.

Revision ID: f3b5d7e9a1c4
Revises: e8a2c4f6b1d3
Create Date: 2026-10-08

New control table ``brand_profiles``: one firm row plus optional company
overrides; NULL columns inherit. No rows are seeded — a firm without a row
shows the built-in Inspro brand, exactly as today.

``clients.card_id_prefix``: the e-card number prefix, frozen from the firm's
brand when a company is created. Existing companies stay NULL ("INS"), so no
card already issued is renumbered.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import sqlite_fk_guard

revision: str = "f3b5d7e9a1c4"
down_revision: str | None = "e8a2c4f6b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "broker_firm_id", sa.String(36),
            sa.ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "client_id", sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=True,
        ),
        sa.Column("scope_key", sa.String(40), nullable=False),
        sa.Column("product_name", sa.String(80), nullable=True),
        sa.Column("short_name", sa.String(30), nullable=True),
        sa.Column("primary_color", sa.String(7), nullable=True),
        sa.Column("accent_color", sa.String(7), nullable=True),
        sa.Column("assets", sa.JSON(), nullable=True),
        sa.Column("support_email", sa.String(320), nullable=True),
        sa.Column("support_phone", sa.String(40), nullable=True),
        sa.Column("email_sender_name", sa.String(120), nullable=True),
        sa.Column("email_reply_to", sa.String(320), nullable=True),
        sa.Column("email_from_address", sa.String(320), nullable=True),
        sa.Column("email_from_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("card_prefix", sa.String(8), nullable=True),
        sa.Column("show_platform_attribution", sa.Boolean(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("broker_firm_id", "scope_key", name="uq_brand_profiles_firm_scope"),
    )
    op.create_index("ix_brand_profiles_broker_firm_id", "brand_profiles", ["broker_firm_id"])
    op.create_index("ix_brand_profiles_client_id", "brand_profiles", ["client_id"])
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("card_id_prefix", sa.String(8), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("clients", recreate="always") as batch:
                batch.drop_column("card_id_prefix")
    else:
        op.drop_column("clients", "card_id_prefix")
    op.drop_index("ix_brand_profiles_client_id", table_name="brand_profiles")
    op.drop_index("ix_brand_profiles_broker_firm_id", table_name="brand_profiles")
    op.drop_table("brand_profiles")
