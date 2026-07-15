"""Member portal identity

Control-plane tables for the employee self-service portal:

- ``member_accounts`` — stable cross-policy-year login identity of one insured
  employee of one client (email OTP auth, no Entra). Lives in ``public``
  because authentication resolves before a firm schema is known.
- ``member_otp_codes`` — hashed one-time sign-in codes.

Tenant/audit additions (additive, nullable — back-compatible):

- ``employees.member_account_id`` — per-year binding to the member account
  (plain String, not a cross-schema FK; auto-provisions to firm schemas).
- ``audit_log.actor_type`` + ``audit_log.member_account_id`` — portal-originated
  events land in the existing audit trail with a member actor.

Revision ID: b5e7a9c1d3f4
Revises: a3c5e7f9b1d2
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e7a9c1d3f4"
down_revision: Union[str, None] = "a3c5e7f9b1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("staff_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="invited"),
        sa.Column("invited_by", sa.String(36), nullable=True),
        sa.Column("last_sign_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("client_id", "email", name="uq_member_accounts_client_email"),
        sa.UniqueConstraint("client_id", "staff_id", name="uq_member_accounts_client_staff"),
    )
    op.create_index("ix_member_accounts_client_id", "member_accounts", ["client_id"])
    op.create_index("ix_member_accounts_email", "member_accounts", ["email"])
    op.create_index("ix_member_accounts_status", "member_accounts", ["status"])

    op.create_table(
        "member_otp_codes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "member_account_id",
            sa.String(36),
            sa.ForeignKey("member_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_member_otp_codes_member_account_id", "member_otp_codes", ["member_account_id"]
    )

    op.add_column("employees", sa.Column("member_account_id", sa.String(36), nullable=True))
    op.create_index("ix_employees_member_account_id", "employees", ["member_account_id"])

    op.add_column("audit_log", sa.Column("actor_type", sa.String(16), nullable=True))
    op.add_column("audit_log", sa.Column("member_account_id", sa.String(36), nullable=True))
    op.create_index("ix_audit_log_member_account_id", "audit_log", ["member_account_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_member_account_id", table_name="audit_log")
    op.drop_column("audit_log", "member_account_id")
    op.drop_column("audit_log", "actor_type")
    op.drop_index("ix_employees_member_account_id", table_name="employees")
    op.drop_column("employees", "member_account_id")
    op.drop_index("ix_member_otp_codes_member_account_id", table_name="member_otp_codes")
    op.drop_table("member_otp_codes")
    op.drop_index("ix_member_accounts_status", table_name="member_accounts")
    op.drop_index("ix_member_accounts_email", table_name="member_accounts")
    op.drop_index("ix_member_accounts_client_id", table_name="member_accounts")
    op.drop_table("member_accounts")
