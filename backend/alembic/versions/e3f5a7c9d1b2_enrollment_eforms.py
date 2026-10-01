"""Online enrolment e-forms: per-period form setup + signed submissions.

Revision ID: e3f5a7c9d1b2
Revises: d8f2a4b6c0e1
Create Date: 2026-10-01

Additive: two new tenant tables. Provisioned firm schemas pick them up through
``sync_firm_schema`` (create-missing-tables), so only public is created here.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e3f5a7c9d1b2"
down_revision: str | None = "d8f2a4b6c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("enrollment_form_configs"):
        op.create_table(
            "enrollment_form_configs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id", sa.String(36),
                sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "policy_year_id", sa.String(36),
                sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "window_id", sa.String(36),
                sa.ForeignKey("enrollment_windows.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("settings", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.String(36), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("window_id", name="uq_enrollment_form_config_window"),
        )
        op.create_index(
            "ix_enrollment_form_configs_client_id", "enrollment_form_configs", ["client_id"]
        )
        op.create_index(
            "ix_enrollment_form_configs_policy_year_id",
            "enrollment_form_configs", ["policy_year_id"],
        )

    if not insp.has_table("enrollment_form_submissions"):
        op.create_table(
            "enrollment_form_submissions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id", sa.String(36),
                sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "policy_year_id", sa.String(36),
                sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "window_id", sa.String(36),
                sa.ForeignKey("enrollment_windows.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column(
                "enrollment_id", sa.String(36),
                sa.ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column(
                "employee_id", sa.String(36),
                sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("reference_no", sa.String(32), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(16), nullable=False, server_default="portal"),
            sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("signature_name", sa.String(255), nullable=True),
            sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("signer_ip", sa.String(64), nullable=True),
            sa.Column("signer_user_agent", sa.String(512), nullable=True),
            sa.Column("content_sha256", sa.String(64), nullable=True),
            sa.Column("document_id", sa.String(36), nullable=True),
            sa.Column("submitted_by_member_id", sa.String(36), nullable=True),
            sa.Column("submitted_by_user_id", sa.String(36), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("acknowledged_by", sa.String(36), nullable=True),
            sa.Column("superseded_by_id", sa.String(36), nullable=True),
            sa.Column("broker_note", sa.Text(), nullable=True),
            *_timestamps(),
            sa.UniqueConstraint("client_id", "reference_no", name="uq_enrollment_form_reference"),
            sa.UniqueConstraint(
                "employee_id", "window_id", "version", name="uq_enrollment_form_version"
            ),
        )
        op.create_index(
            "ix_enrollment_form_submissions_client_id",
            "enrollment_form_submissions", ["client_id"],
        )
        op.create_index(
            "ix_enrollment_form_submissions_policy_year_id",
            "enrollment_form_submissions", ["policy_year_id"],
        )
        op.create_index(
            "ix_enrollment_form_submissions_window_status",
            "enrollment_form_submissions", ["window_id", "status"],
        )
        op.create_index(
            "ix_enrollment_form_submissions_employee",
            "enrollment_form_submissions", ["employee_id", "version"],
        )


def downgrade() -> None:
    op.drop_table("enrollment_form_submissions")
    op.drop_table("enrollment_form_configs")
