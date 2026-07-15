"""identity: users, user_client_access, invitations

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-05-26 13:00:00.000000

Control-plane identity tables. DB-backed users replace custom Entra claims as
the tenant binding: a signed-in user is matched to a `users` row by Entra
`oid` or email. Broker-role users reach the whole firm; client-role users are
pinned to clients via `user_client_access`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("broker_firm_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["broker_firm_id"], ["broker_firms.id"],
            name=op.f("fk_users_broker_firm_id_broker_firms"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("external_id", name=op.f("uq_users_external_id")),
    )
    op.create_index(op.f("ix_users_broker_firm_id"), "users", ["broker_firm_id"])
    op.create_index(op.f("ix_users_external_id"), "users", ["external_id"])
    op.create_index(op.f("ix_users_email"), "users", ["email"])
    op.create_index(op.f("ix_users_status"), "users", ["status"])

    op.create_table(
        "user_client_access",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_user_client_access_user_id_users"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"],
            name=op.f("fk_user_client_access_client_id_clients"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_client_access")),
        sa.UniqueConstraint(
            "user_id", "client_id", name="uq_user_client_access_user_client"
        ),
    )
    op.create_index(
        op.f("ix_user_client_access_user_id"), "user_client_access", ["user_id"]
    )
    op.create_index(
        op.f("ix_user_client_access_client_id"), "user_client_access", ["client_id"]
    )

    op.create_table(
        "invitations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("broker_firm_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("invited_by", sa.String(length=36), nullable=True),
        sa.Column("client_ids", json_variant(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["broker_firm_id"], ["broker_firms.id"],
            name=op.f("fk_invitations_broker_firm_id_broker_firms"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint("token", name=op.f("uq_invitations_token")),
    )
    op.create_index(op.f("ix_invitations_email"), "invitations", ["email"])
    op.create_index(op.f("ix_invitations_broker_firm_id"), "invitations", ["broker_firm_id"])
    op.create_index(op.f("ix_invitations_token"), "invitations", ["token"])
    op.create_index(op.f("ix_invitations_status"), "invitations", ["status"])


def downgrade() -> None:
    op.drop_table("invitations")
    op.drop_table("user_client_access")
    op.drop_table("users")
