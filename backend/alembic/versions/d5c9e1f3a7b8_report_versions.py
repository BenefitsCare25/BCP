"""Retained report versions (Reports Center).

Persists a generated report as an immutable (versioned) or supersede-in-place
(latest) record — bytes in the storage backend, metadata + membership manifest
here. See ``services/report_versions.py``.

Revision ID: d5c9e1f3a7b8
Revises: c1e3a5b7d9f2
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "d5c9e1f3a7b8"
down_revision = "c1e3a5b7d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("report_type", sa.String(48), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("params", json_variant(), nullable=False),
        sa.Column("summary", json_variant(), nullable=False),
        sa.Column("manifest", json_variant(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("generated_by_user_id", sa.String(36), nullable=True),
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
    op.create_index(
        "ix_report_versions_series",
        "report_versions",
        ["client_id", "policy_year_id", "report_type", "scope_key", "version_no"],
    )


def downgrade() -> None:
    op.drop_index("ix_report_versions_series", table_name="report_versions")
    op.drop_table("report_versions")
