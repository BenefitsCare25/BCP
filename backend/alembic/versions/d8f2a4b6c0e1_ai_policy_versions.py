"""Platform AI policy documents and immutable file versions.

Revision ID: d8f2a4b6c0e1
Revises: c7e1a3f5b9d2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d8f2a4b6c0e1"
down_revision = "c7e1a3f5b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Firm provisioning creates shared control tables from current metadata.
    # An existing firm may therefore have this table before Alembic reaches
    # this revision. Preserve that table and its version records.
    if sa.inspect(op.get_bind()).has_table("ai_policy_versions"):
        return
    op.create_table(
        "ai_policy_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("policy_id", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("review_due", sa.Date(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(320), nullable=False),
        sa.Column("uploaded_by_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_by", sa.String(320), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by", sa.String(320), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("policy_id", "version", name="uq_ai_policy_version"),
    )
    op.create_index("ix_ai_policy_versions_policy_id", "ai_policy_versions", ["policy_id"])
    op.create_index(
        "uq_ai_policy_published", "ai_policy_versions", ["policy_id"], unique=True,
        postgresql_where=sa.text("status = 'published'"),
        sqlite_where=sa.text("status = 'published'"),
    )


def downgrade() -> None:
    op.drop_table("ai_policy_versions")
