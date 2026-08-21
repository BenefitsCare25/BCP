"""Claim-type-scoped document setups and claim requirement snapshots.

Revision ID: c6e8a0b2d4f7
Revises: b5d7f9a1c3e6
Create Date: 2026-08-21

Additive schema migration. Existing claims retain NULL snapshots and use the
legacy/default resolver; no data rewrite or table lock is required.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant

revision = "c6e8a0b2d4f7"
down_revision = "b5d7f9a1c3e6"
branch_labels = None
depends_on = None


def _table_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("claim_kind", sa.String(16), nullable=False),
        sa.Column("claim_key", sa.String(128), nullable=False),
        sa.Column("scope_code", sa.String(64), nullable=False),
        sa.Column("display_label", sa.String(128), nullable=False),
        sa.Column("documents", json_variant(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "client_id", "claim_kind", "claim_key", "scope_code",
            name="uq_claim_document_setup_client_type_scope",
        ),
    ]


def _postgres_firm_schemas(bind) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    return [
        "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        for firm_id in bind.execute(
            sa.text("SELECT id FROM public.broker_firms")
        ).scalars()
    ]


def upgrade() -> None:
    op.create_table("claim_document_setups", *_table_columns())
    op.create_index(
        "ix_claim_document_setups_client_id",
        "claim_document_setups",
        ["client_id"],
    )
    op.add_column(
        "claims",
        sa.Column("required_documents_snapshot", json_variant(), nullable=True),
    )

    bind = op.get_bind()
    for schema in _postgres_firm_schemas(bind):
        if not bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.claims"},
        ):
            continue
        bind.execute(
            sa.text(
                f'CREATE TABLE IF NOT EXISTS "{schema}".claim_document_setups ('
                "id VARCHAR(36) PRIMARY KEY, "
                "client_id VARCHAR(36) NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE, "
                "claim_kind VARCHAR(16) NOT NULL, claim_key VARCHAR(128) NOT NULL, "
                "scope_code VARCHAR(64) NOT NULL, display_label VARCHAR(128) NOT NULL, "
                "documents JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT uq_claim_document_setup_client_type_scope UNIQUE "
                "(client_id, claim_kind, claim_key, scope_code))"
            )
        )
        bind.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_claim_document_setups_client_id '
                f'ON "{schema}".claim_document_setups (client_id)'
            )
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".claims ADD COLUMN IF NOT EXISTS '
                "required_documents_snapshot JSONB"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for schema in _postgres_firm_schemas(bind):
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".claims DROP COLUMN IF EXISTS '
                "required_documents_snapshot"
            )
        )
        bind.execute(
            sa.text(f'DROP TABLE IF EXISTS "{schema}".claim_document_setups')
        )
    op.drop_column("claims", "required_documents_snapshot")
    op.drop_index(
        "ix_claim_document_setups_client_id", table_name="claim_document_setups"
    )
    op.drop_table("claim_document_setups")
