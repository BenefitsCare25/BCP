"""Add enrollment module tables

Enrollment windows + per-member enrollments/elections, sparse per-employee plan
overrides (the effective-coverage state), leave policy + elections (days only),
and a bulk plan-update audit record. All additive — new tables only — so the
per-firm schema sync (provision_tenants) picks them up without bespoke steps.

Revision ID: d1e2f3a4b5c6
Revises: c3d4e5f6a7b8
Create Date: 2026-06-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts_cols() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "enrollment_windows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("window_type", sa.String(32), nullable=False, server_default="open"),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("default_behavior", sa.String(32), nullable=False, server_default="deemed_keep_current"),
        sa.Column("allow_plan_change", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("allow_leave", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("allow_dependant_changes", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("product_scope", json_variant(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        *_ts_cols(),
    )
    op.create_index("ix_enrollment_windows_policy_year_id", "enrollment_windows", ["policy_year_id"])
    op.create_index("ix_enrollment_windows_client_id", "enrollment_windows", ["client_id"])
    op.create_index("ix_enrollment_windows_status", "enrollment_windows", ["status"])
    op.create_index("ix_enrollment_windows_year_status", "enrollment_windows", ["policy_year_id", "status"])

    op.create_table(
        "enrollments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("window_id", sa.String(36), sa.ForeignKey("enrollment_windows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="not_started"),
        sa.Column("baseline_snapshot", json_variant(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", sa.String(36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by", sa.String(36), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("window_id", "employee_id", name="uq_enrollment_window_employee"),
    )
    op.create_index("ix_enrollments_window_id", "enrollments", ["window_id"])
    op.create_index("ix_enrollments_policy_year_id", "enrollments", ["policy_year_id"])
    op.create_index("ix_enrollments_client_id", "enrollments", ["client_id"])
    op.create_index("ix_enrollments_employee_id", "enrollments", ["employee_id"])
    op.create_index("ix_enrollments_status", "enrollments", ["status"])

    op.create_table(
        "enrollment_elections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enrollment_id", sa.String(36), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("previous_plan_code", sa.String(64), nullable=True),
        sa.Column("elected_plan_code", sa.String(64), nullable=True),
        sa.Column("action", sa.String(32), nullable=False, server_default="keep"),
        sa.Column("covered_dependant_ids", json_variant(), nullable=True),
        sa.Column("notes", sa.String(1024), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("enrollment_id", "product_id", name="uq_election_enrollment_product"),
    )
    op.create_index("ix_enrollment_elections_enrollment_id", "enrollment_elections", ["enrollment_id"])
    op.create_index("ix_enrollment_elections_policy_year_id", "enrollment_elections", ["policy_year_id"])
    op.create_index("ix_enrollment_elections_client_id", "enrollment_elections", ["client_id"])
    op.create_index("ix_enrollment_elections_product_id", "enrollment_elections", ["product_id"])

    op.create_table(
        "employee_plan_overrides",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("plan_code", sa.String(64), nullable=True),
        sa.Column("declined", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("covered_dependant_ids", json_variant(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual_admin"),
        sa.Column("source_ref", sa.String(36), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("modified_by", sa.String(36), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("employee_id", "product_id", name="uq_override_employee_product"),
    )
    op.create_index("ix_employee_plan_overrides_employee_id", "employee_plan_overrides", ["employee_id"])
    op.create_index("ix_employee_plan_overrides_policy_year_id", "employee_plan_overrides", ["policy_year_id"])
    op.create_index("ix_employee_plan_overrides_client_id", "employee_plan_overrides", ["client_id"])
    op.create_index("ix_employee_plan_overrides_product_id", "employee_plan_overrides", ["product_id"])

    op.create_table(
        "leave_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allow_buy", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("allow_sell", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("min_buy_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_buy_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("min_sell_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_sell_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("increment_days", sa.Float(), nullable=False, server_default="1"),
        sa.Column("notes", sa.String(1024), nullable=True),
        *_ts_cols(),
        sa.UniqueConstraint("policy_year_id", name="uq_leave_policy_year"),
    )
    op.create_index("ix_leave_policies_policy_year_id", "leave_policies", ["policy_year_id"])
    op.create_index("ix_leave_policies_client_id", "leave_policies", ["client_id"])

    op.create_table(
        "leave_elections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enrollment_id", sa.String(36), sa.ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(32), nullable=False, server_default="none"),
        sa.Column("days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        *_ts_cols(),
        sa.UniqueConstraint("enrollment_id", name="uq_leave_election_enrollment"),
    )
    op.create_index("ix_leave_elections_enrollment_id", "leave_elections", ["enrollment_id"])
    op.create_index("ix_leave_elections_policy_year_id", "leave_elections", ["policy_year_id"])
    op.create_index("ix_leave_elections_client_id", "leave_elections", ["client_id"])
    op.create_index("ix_leave_elections_employee_id", "leave_elections", ["employee_id"])

    op.create_table(
        "bulk_plan_updates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_year_id", sa.String(36), sa.ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("initiated_by", sa.String(36), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("target_plan_code", sa.String(64), nullable=True),
        sa.Column("action", sa.String(32), nullable=False, server_default="set_plan"),
        sa.Column("selector", json_variant(), nullable=False),
        sa.Column("dependant_action", json_variant(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="applied"),
        sa.Column("result_summary", json_variant(), nullable=False),
        *_ts_cols(),
    )
    op.create_index("ix_bulk_plan_updates_policy_year_id", "bulk_plan_updates", ["policy_year_id"])
    op.create_index("ix_bulk_plan_updates_client_id", "bulk_plan_updates", ["client_id"])


def downgrade() -> None:
    op.drop_table("bulk_plan_updates")
    op.drop_table("leave_elections")
    op.drop_table("leave_policies")
    op.drop_table("employee_plan_overrides")
    op.drop_table("enrollment_elections")
    op.drop_table("enrollments")
    op.drop_table("enrollment_windows")
