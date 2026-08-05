"""claims: case_type / origin / created_by_user_id / intake_meta (LOG cases)

Additive only — four columns plus an index on an existing table, which is all
``provision_tenants.py`` can sync into the per-firm Postgres schemas.

``claims`` is a TENANT table: on Postgres this migration runs against ``public``
only, and `db/tenancy.sync_firm_schema` is what carries the change into each
``firm_<id>`` schema. It reconciles columns AND indexes from **model metadata**,
so the index below is also declared on ``Claim.__table_args__`` — a
migration-only index would exist solely on the empty ``public.claims``.

Deliberately NOT wrapped in ``batch_alter_table``: the app sets
``PRAGMA foreign_keys=ON`` per connection, so a batch (table-recreate) migration
on SQLite fires ON DELETE CASCADE into ``stored_documents`` and
``claim_messages`` — it would take every claim's documents and its whole message
thread with it. Plain ``add_column`` needs no recreate.

Both ``server_default``s are also the correct backfill: every row that exists
today IS a member-submitted reimbursement claim. They are kept permanently —
dropping a server default later needs exactly the batch migration ruled out
above.

Revision ID: c5e7a9b1d3f4
Revises: a1c3e5b7d9f2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "c5e7a9b1d3f4"
down_revision = "a1c3e5b7d9f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column(
            "case_type",
            sa.String(length=16),
            nullable=False,
            server_default="claim",
        ),
    )
    op.add_column(
        "claims",
        sa.Column(
            "origin",
            sa.String(length=16),
            nullable=False,
            server_default="portal",
        ),
    )
    op.add_column(
        "claims",
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
    )
    op.add_column("claims", sa.Column("intake_meta", json_variant(), nullable=True))
    # The broker queue and the employee-level card both filter on case type
    # within a policy year; the portal filters on origin within an employee.
    op.create_index(
        "ix_claims_year_case_type", "claims", ["policy_year_id", "case_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_claims_year_case_type", table_name="claims")
    op.drop_column("claims", "intake_meta")
    op.drop_column("claims", "created_by_user_id")
    op.drop_column("claims", "origin")
    op.drop_column("claims", "case_type")
