"""claims.visit_type + stored_documents.doc_type.

Revision ID: b9e1d3f5a7c2
Revises: e4a6c8b0d2f1
Create Date: 2026-07-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b9e1d3f5a7c2"
down_revision = "e4a6c8b0d2f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("visit_type", sa.String(16), nullable=True),
    )
    op.add_column(
        "stored_documents",
        sa.Column("doc_type", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stored_documents", "doc_type")
    op.drop_column("claims", "visit_type")
