"""member_enquiries + claim_messages.enquiry_id (questions with no claim)

Revision ID: a3f7c9d21b48
Revises: c1e5a7d40b93
Create Date: 2026-08-06

Three changes, and only two of them are the kind Alembic normally does here:

1. CREATE `member_enquiries` — a new tenant table. Additive;
   `scripts/provision_tenants.py` propagates it to every firm schema on deploy.
2. ADD `claim_messages.enquiry_id` — a new column. Additive, same story.
3. `claim_messages.claim_id` DROP NOT NULL — **not additive, and the reason this
   migration is more than four lines.**

`provision_tenants` syncs new TABLES and new COLUMNS only (its own docstring
says so, and CLAUDE.md lists drops/renames/type changes as unsupported). A plain
`op.alter_column` runs against the migration's own `search_path` — `public` —
so every `firm_<id>.claim_messages` would keep `NOT NULL`, and the first
question posted in prod would fail on an integrity error that no SQLite test can
reproduce. So the ALTER is applied per schema, explicitly.

On SQLite the same change is a `batch_alter_table`, which recreates the table.
The app sets `PRAGMA foreign_keys=ON` per connection, so that DROP fires every
`ON DELETE CASCADE` pointing at `claim_messages` — hence `sqlite_fk_guard`.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import sqlite_fk_guard
from app.db.tenancy import schema_for_firm

revision = "a3f7c9d21b48"
down_revision = "c1e5a7d40b93"
branch_labels = None
depends_on = None


def _firm_schemas(bind) -> list[str]:
    """Every schema holding a copy of `claim_messages`, `public` first.

    WHICH firms exist is read from the database. What their schemas are CALLED
    comes from `schema_for_firm` — the same helper `sync_firm_schema` used to
    create them. Spelling the rule out again here is how this silently targets
    schemas that do not exist: it strips every non-alphanumeric character, not
    just the dashes a uuid makes obvious, so a hand-rolled copy misses on the
    first firm id that contains anything else, finds no table, skips the ALTER,
    and leaves prod with the `NOT NULL` this migration exists to remove.
    """
    rows = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars().all()
    return ["public", *(schema_for_firm(str(fid)) for fid in rows)]


def _schema_has_table(bind, schema: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = 'claim_messages'"
            ),
            {"s": schema},
        ).first()
    )


def _set_claim_id_nullable(bind, *, nullable: bool) -> None:
    verb = "DROP NOT NULL" if nullable else "SET NOT NULL"
    for schema in _firm_schemas(bind):
        if not _schema_has_table(bind, schema):
            continue
        bind.exec_driver_sql(
            f'ALTER TABLE "{schema}".claim_messages ALTER COLUMN claim_id {verb}'
        )


def upgrade() -> None:
    op.create_table(
        "member_enquiries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            sa.String(36),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="open"
        ),
        sa.Column(
            "about_claim_id",
            sa.String(36),
            sa.ForeignKey("claims.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_member_enquiries_client_id", "member_enquiries", ["client_id"]
    )
    op.create_index(
        "ix_member_enquiries_employee_id", "member_enquiries", ["employee_id"]
    )
    op.create_index(
        "ix_member_enquiries_policy_year_id", "member_enquiries", ["policy_year_id"]
    )
    op.create_index(
        "ix_member_enquiries_about_claim_id", "member_enquiries", ["about_claim_id"]
    )
    op.create_index(
        "ix_member_enquiries_employee_year",
        "member_enquiries",
        ["employee_id", "policy_year_id"],
    )
    op.create_index(
        "ix_member_enquiries_client_status",
        "member_enquiries",
        ["client_id", "status"],
    )

    op.add_column(
        "claim_messages", sa.Column("enquiry_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_claim_messages_enquiry_id", "claim_messages", ["enquiry_id"]
    )
    op.create_index(
        "ix_claim_messages_enquiry_created",
        "claim_messages",
        ["enquiry_id", "created_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The FK is created here rather than in `add_column` so the per-schema
        # ALTER below and this constraint apply to the same, already-existing
        # column — and so SQLite (which cannot ADD a foreign key) takes the
        # batch path instead.
        op.create_foreign_key(
            "fk_claim_messages_enquiry_id",
            "claim_messages",
            "member_enquiries",
            ["enquiry_id"],
            ["id"],
            ondelete="CASCADE",
        )
        _set_claim_id_nullable(bind, nullable=True)
    else:
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("claim_messages") as batch:
                batch.alter_column(
                    "claim_id", existing_type=sa.String(36), nullable=True
                )
                batch.create_foreign_key(
                    "fk_claim_messages_enquiry_id",
                    "member_enquiries",
                    ["enquiry_id"],
                    ["id"],
                    ondelete="CASCADE",
                )


def downgrade() -> None:
    """Reverses the schema. It does NOT delete question messages — a downgrade
    that silently discarded members' conversations would be worse than one that
    refuses, so `SET NOT NULL` will fail loudly if any exist. Delete them
    deliberately first if that is really what is wanted.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _set_claim_id_nullable(bind, nullable=False)
        op.drop_constraint(
            "fk_claim_messages_enquiry_id", "claim_messages", type_="foreignkey"
        )
    else:
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("claim_messages") as batch:
                batch.drop_constraint(
                    "fk_claim_messages_enquiry_id", type_="foreignkey"
                )
                batch.alter_column(
                    "claim_id", existing_type=sa.String(36), nullable=False
                )
    op.drop_index("ix_claim_messages_enquiry_created", table_name="claim_messages")
    op.drop_index("ix_claim_messages_enquiry_id", table_name="claim_messages")
    op.drop_column("claim_messages", "enquiry_id")
    op.drop_table("member_enquiries")
