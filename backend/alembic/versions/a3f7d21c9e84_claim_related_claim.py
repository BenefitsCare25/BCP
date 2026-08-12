"""claims.related_claim_id — the visit a claim continues

Revision ID: a3f7d21c9e84
Revises: e2c9a41f7b60
Create Date: 2026-08-12

The hospital admission a pre-/post-hospitalisation consult belongs to, or the
first visit of a specialist course. Nullable with no backfill: nothing can infer
the link for claims filed before it existed, and it is never required — a member
must be able to file a claim with no anchor at all.

Plain column, not a self-FK — see the model comment: `tenancy.sync_firm_schema`
adds columns without their REFERENCES clause, so an FK here would exist in some
firm schemas and not others. Eligibility is validated in
`services/claim_episodes.py`, which is far narrower than referential integrity
anyway.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f7d21c9e84"
down_revision = "e2c9a41f7b60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims", sa.Column("related_claim_id", sa.String(36), nullable=True)
    )
    op.create_index("ix_claims_related_claim", "claims", ["related_claim_id"])


def downgrade() -> None:
    op.drop_index("ix_claims_related_claim", table_name="claims")
    op.drop_column("claims", "related_claim_id")
