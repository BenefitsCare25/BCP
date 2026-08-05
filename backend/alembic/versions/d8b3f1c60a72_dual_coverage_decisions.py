"""dual coverage decisions

A TENANT table (per-firm schema on Postgres). Additive only — a plain
``create_table`` never triggers SQLite's copy-and-rename, so the
``sqlite_fk_guard`` batch pattern does not apply here.

**Deploy note:** a new tenant table means ``scripts/provision_tenants.py`` must
run this release so the table lands in every firm schema. Without it,
``set_search_path`` falls through to ``public`` and every firm silently shares
one table — no error, just cross-tenant rows.

Revision ID: d8b3f1c60a72
Revises: c5e7a9b1d3f4
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "d8b3f1c60a72"
down_revision = "c5e7a9b1d3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dual_coverage_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(32), nullable=False, server_default="life"),
        sa.Column("subject_key", sa.String(64), nullable=False),
        sa.Column("subject_keys", json_variant(), nullable=True),
        sa.Column("decision", sa.String(32), nullable=False),
        # SET NULL, not CASCADE: a roster wipe must not delete the broker's
        # decisions. `carried_by_staff_id` keeps the record readable afterwards.
        sa.Column(
            "carried_by_employee_id",
            sa.String(36),
            sa.ForeignKey("employees.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("carried_by_staff_id", sa.String(128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.String(255), nullable=True),
        sa.Column("decided_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("parties_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "policy_year_id",
            "subject_key",
            name="uq_dual_coverage_decision_year_subject",
        ),
    )
    op.create_index(
        "ix_dual_coverage_decisions_client_id",
        "dual_coverage_decisions",
        ["client_id"],
    )
    op.create_index(
        "ix_dual_coverage_decisions_policy_year_id",
        "dual_coverage_decisions",
        ["policy_year_id"],
    )
    op.create_index(
        "ix_dual_coverage_decisions_subject_key",
        "dual_coverage_decisions",
        ["subject_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dual_coverage_decisions_subject_key", table_name="dual_coverage_decisions"
    )
    op.drop_index(
        "ix_dual_coverage_decisions_policy_year_id",
        table_name="dual_coverage_decisions",
    )
    op.drop_index(
        "ix_dual_coverage_decisions_client_id", table_name="dual_coverage_decisions"
    )
    op.drop_table("dual_coverage_decisions")
