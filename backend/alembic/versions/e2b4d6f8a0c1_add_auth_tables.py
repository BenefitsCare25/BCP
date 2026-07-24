"""Local-credential auth tables: credentials, MFA, sessions, events, policy.

All control-plane (public). See docs/AUTH_DESIGN.md §4.

Revision ID: e2b4d6f8a0c1
Revises: d1f3a5c7e9b2
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "e2b4d6f8a0c1"
down_revision = "d1f3a5c7e9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auth_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "broker_firm_id",
            sa.String(36),
            sa.ForeignKey("broker_firms.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("hr_login_id", sa.String(32), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "password_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("must_rotate_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", name="uq_auth_credentials_user_id"),
        sa.UniqueConstraint(
            "broker_firm_id", "hr_login_id", name="uq_auth_credentials_firm_login_id"
        ),
    )
    op.create_index(
        "ix_auth_credentials_user_id", "auth_credentials", ["user_id"]
    )
    op.create_index(
        "ix_auth_credentials_broker_firm_id", "auth_credentials", ["broker_firm_id"]
    )

    op.create_table(
        "auth_mfa",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column("totp_secret_enc", sa.Text(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_codes", json_variant(), nullable=True),
        sa.Column("last_used_step", sa.Integer(), nullable=True),
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
        sa.UniqueConstraint("subject_type", "subject_id", name="uq_auth_mfa_subject"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("subject_type", sa.String(16), nullable=False),
        sa.Column("subject_id", sa.String(36), nullable=False),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("broker_firm_id", sa.String(36), nullable=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("refresh_hash", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=True),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("subdomain", sa.String(255), nullable=True),
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
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    op.create_index("ix_auth_sessions_refresh_hash", "auth_sessions", ["refresh_hash"])
    op.create_index("ix_auth_sessions_client_id", "auth_sessions", ["client_id"])
    op.create_index(
        "ix_auth_sessions_subject", "auth_sessions", ["subject_type", "subject_id"]
    )

    op.create_table(
        "auth_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("subject_type", sa.String(16), nullable=True),
        sa.Column("subject_id", sa.String(36), nullable=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("broker_firm_id", sa.String(36), nullable=True),
        sa.Column("identifier_hash", sa.String(64), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("subdomain", sa.String(255), nullable=True),
        sa.Column("detail", json_variant(), nullable=True),
    )
    op.create_index("ix_auth_events_occurred_at", "auth_events", ["occurred_at"])
    op.create_index("ix_auth_events_event_type", "auth_events", ["event_type"])
    op.create_index("ix_auth_events_client_id", "auth_events", ["client_id"])

    op.create_table(
        "client_auth_policy",
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "mfa_hr_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "mfa_portal_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "hr_login_source", sa.String(16), nullable=False, server_default="email"
        ),
        sa.Column(
            "portal_login_source", sa.String(16), nullable=False, server_default="email"
        ),
        sa.Column(
            "password_min_entropy", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("password_rotation_days", sa.Integer(), nullable=True),
        sa.Column(
            "session_idle_minutes", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column(
            "session_absolute_hours", sa.Integer(), nullable=False, server_default="12"
        ),
        sa.Column(
            "breach_check_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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


def downgrade() -> None:
    op.drop_table("client_auth_policy")
    op.drop_index("ix_auth_events_client_id", table_name="auth_events")
    op.drop_index("ix_auth_events_event_type", table_name="auth_events")
    op.drop_index("ix_auth_events_occurred_at", table_name="auth_events")
    op.drop_table("auth_events")
    op.drop_index("ix_auth_sessions_subject", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_client_id", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_refresh_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_family_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("auth_mfa")
    op.drop_index("ix_auth_credentials_broker_firm_id", table_name="auth_credentials")
    op.drop_index("ix_auth_credentials_user_id", table_name="auth_credentials")
    op.drop_table("auth_credentials")
