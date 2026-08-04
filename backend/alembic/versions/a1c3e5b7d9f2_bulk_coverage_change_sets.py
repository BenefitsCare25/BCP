"""bulk_plan_updates: change sets, acknowledgements, undo, idempotency

Additive only — new nullable columns on an existing table, which is all
``provision_tenants.py`` can sync into the per-firm Postgres schemas.

The flat ``product_code`` / ``target_plan_code`` / ``action`` / ``selector`` /
``dependant_action`` columns are deliberately left in place and still written
(from the first change), so pre-change-set rows stay readable.

Revision ID: a1c3e5b7d9f2
Revises: d3f7a1c95e82
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "a1c3e5b7d9f2"
down_revision = "d3f7a1c95e82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("bulk_plan_updates", sa.Column("query", json_variant(), nullable=True))
    op.add_column("bulk_plan_updates", sa.Column("changes", json_variant(), nullable=True))
    op.add_column(
        "bulk_plan_updates", sa.Column("acknowledged", json_variant(), nullable=True)
    )
    op.add_column(
        "bulk_plan_updates", sa.Column("undo_of", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "bulk_plan_updates", sa.Column("request_id", sa.String(length=64), nullable=True)
    )
    # UNIQUE, both of them. Each guards a check-then-act that is otherwise a
    # race: two concurrent applies sharing a request_id both miss the existing
    # row and both write, and two concurrent undos of the same batch both pass
    # the "already undone" lookup. NULLs are distinct in a unique index on both
    # SQLite and Postgres, so the many rows carrying neither value are fine.
    op.create_index(
        "ix_bulk_plan_updates_undo_of", "bulk_plan_updates", ["undo_of"], unique=True
    )
    # Idempotency lookups are always scoped to the client, so the index leads on
    # it — a bare request_id index would still scan a tenant's rows on Postgres,
    # where every firm has its own copy of this table.
    op.create_index(
        "ix_bulk_plan_updates_request",
        "bulk_plan_updates",
        ["client_id", "request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_bulk_plan_updates_request", table_name="bulk_plan_updates")
    op.drop_index("ix_bulk_plan_updates_undo_of", table_name="bulk_plan_updates")
    op.drop_column("bulk_plan_updates", "request_id")
    op.drop_column("bulk_plan_updates", "undo_of")
    op.drop_column("bulk_plan_updates", "acknowledged")
    op.drop_column("bulk_plan_updates", "changes")
    op.drop_column("bulk_plan_updates", "query")
