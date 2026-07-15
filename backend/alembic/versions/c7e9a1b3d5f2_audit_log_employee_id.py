"""Add employee_id to audit_log for per-member coverage history

Adds a nullable, indexed ``employee_id`` to ``audit_log`` so the per-employee
coverage-history (track) view can filter member-scoped events on an indexed
column instead of scanning JSON payloads. Additive + nullable — back-compatible;
existing rows keep ``employee_id = NULL`` (history is forward-only).

Revision ID: c7e9a1b3d5f2
Revises: b4d6f8a0c2e5
Create Date: 2026-06-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e9a1b3d5f2"
down_revision: Union[str, None] = "b4d6f8a0c2e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_log",
        sa.Column("employee_id", sa.String(36), nullable=True),
    )
    op.create_index("ix_audit_log_employee_id", "audit_log", ["employee_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_employee_id", table_name="audit_log")
    op.drop_column("audit_log", "employee_id")
