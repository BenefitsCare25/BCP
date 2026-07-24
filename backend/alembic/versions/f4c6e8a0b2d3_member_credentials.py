"""Member credential login: password + system_login_id; email nullable.

Employees sign in with a username (email / system-generated id / staff id) +
Argon2id password. Email becomes nullable (not every employee has one).

Revision ID: f4c6e8a0b2d3
Revises: e2b4d6f8a0c1
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import sqlite_fk_guard

revision = "f4c6e8a0b2d3"
down_revision = "e2b4d6f8a0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite recreates the table for the nullable/constraint changes, which
    # fires member_otp_codes' ON DELETE CASCADE — the guard suspends FK
    # enforcement for the duration. No-op on Postgres.
    with sqlite_fk_guard(op.get_bind()):
        with op.batch_alter_table("member_accounts") as batch:
            batch.add_column(sa.Column("system_login_id", sa.String(32), nullable=True))
            batch.add_column(sa.Column("password_hash", sa.String(255), nullable=True))
            batch.add_column(
                sa.Column("password_updated_at", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(
                sa.Column("must_rotate_after", sa.DateTime(timezone=True), nullable=True)
            )
            batch.add_column(
                sa.Column(
                    "failed_attempts", sa.Integer(), nullable=False, server_default="0"
                )
            )
            batch.add_column(
                sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
            )
            batch.alter_column("email", existing_type=sa.String(320), nullable=True)
            batch.create_unique_constraint(
                "uq_member_accounts_client_system_id", ["client_id", "system_login_id"]
            )


def downgrade() -> None:
    with sqlite_fk_guard(op.get_bind()):
        with op.batch_alter_table("member_accounts") as batch:
            batch.drop_constraint(
                "uq_member_accounts_client_system_id", type_="unique"
            )
            batch.alter_column("email", existing_type=sa.String(320), nullable=False)
            batch.drop_column("locked_until")
            batch.drop_column("failed_attempts")
            batch.drop_column("must_rotate_after")
            batch.drop_column("password_updated_at")
            batch.drop_column("password_hash")
            batch.drop_column("system_login_id")
