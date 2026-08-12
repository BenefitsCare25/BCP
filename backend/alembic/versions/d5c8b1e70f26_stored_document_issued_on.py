"""stored_documents.issued_on — the date a document states it was issued

Revision ID: d5c8b1e70f26
Revises: b6d4a08f13c7
Create Date: 2026-08-12

Distinct from `created_at`, which is the upload time. Populated by the
referral-letter flow, where the letter's own date is what an insurer measures
its validity from. Nullable with no backfill: nothing can infer the issue date
of a letter already on file, and the age rule skips a NULL rather than reading
the upload date as a stand-in.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5c8b1e70f26"
down_revision = "b6d4a08f13c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stored_documents", sa.Column("issued_on", sa.Date(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("stored_documents", "issued_on")
