"""BYOK: per-client AI provider configuration (encrypted API key)

Revision ID: c4d8e9f12345
Revises: b2f1c5a7d402
Create Date: 2026-05-14 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e9f12345"
down_revision: Union[str, None] = "b2f1c5a7d402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_ai_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.String(length=512), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("encrypted_api_key", sa.LargeBinary(), nullable=False),
        sa.Column("key_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validation_error", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name="fk_client_ai_configs_client_id_clients",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_client_ai_configs"),
        sa.UniqueConstraint("client_id", name="uq_client_ai_configs_client_id"),
    )
    op.create_index(
        "ix_client_ai_configs_client_id", "client_ai_configs", ["client_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_client_ai_configs_client_id", table_name="client_ai_configs")
    op.drop_table("client_ai_configs")
