"""Stable claim subtype scopes for review and document configuration.

Revision ID: b5d7f9a1c3e6
Revises: a4c6e8f0b2d4
Create Date: 2026-08-20

Existing claim-review rows become product/category defaults (`scope_code='*'`).
Exact subtype overrides are additive. Document routing targets are nullable so
legacy seeded rows can use their in-code defaults until explicitly re-saved.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant, sqlite_fk_guard

revision = "b5d7f9a1c3e6"
down_revision = "a4c6e8f0b2d4"
branch_labels = None
depends_on = None

_OLD_UQ = "uq_claim_review_config_client_type"
_NEW_UQ = "uq_claim_review_config_client_type_scope"


def _postgres_schemas(bind) -> list[str]:
    schemas = ["public"]
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.claim_review_configs"},
        ):
            schemas.append(schema)
    return schemas


def _upgrade_postgres(bind, schema: str) -> None:
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_review_configs '
            "ADD COLUMN IF NOT EXISTS scope_code VARCHAR(64) NOT NULL DEFAULT '*'"
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_doc_types '
            "ADD COLUMN IF NOT EXISTS claim_scope_keys JSONB"
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_review_configs '
            f'DROP CONSTRAINT IF EXISTS "{_OLD_UQ}"'
        )
    )
    exists = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint c "
            "JOIN pg_namespace n ON n.oid=c.connamespace "
            "WHERE n.nspname=:schema AND c.conname=:name)"
        ),
        {"schema": schema, "name": _NEW_UQ},
    )
    if not exists:
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".claim_review_configs '
                f'ADD CONSTRAINT "{_NEW_UQ}" UNIQUE '
                "(client_id, claim_kind, claim_key, scope_code)"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for schema in _postgres_schemas(bind):
            _upgrade_postgres(bind, schema)
        return
    with sqlite_fk_guard(bind):
        with op.batch_alter_table("claim_review_configs", recreate="always") as batch:
            batch.add_column(
                sa.Column(
                    "scope_code",
                    sa.String(64),
                    nullable=False,
                    server_default="*",
                )
            )
            batch.drop_constraint(_OLD_UQ, type_="unique")
            batch.create_unique_constraint(
                _NEW_UQ,
                ["client_id", "claim_kind", "claim_key", "scope_code"],
            )
        with op.batch_alter_table("claim_doc_types", recreate="always") as batch:
            batch.add_column(sa.Column("claim_scope_keys", json_variant(), nullable=True))


def _downgrade_postgres(bind, schema: str) -> None:
    exact_count = bind.scalar(
        sa.text(
            f'SELECT count(*) FROM "{schema}".claim_review_configs '
            "WHERE scope_code <> '*'"
        )
    )
    if exact_count:
        raise RuntimeError(
            f"Cannot downgrade {schema}: exact subtype review configs exist."
        )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_review_configs '
            f'DROP CONSTRAINT IF EXISTS "{_NEW_UQ}"'
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_review_configs '
            f'ADD CONSTRAINT "{_OLD_UQ}" UNIQUE '
            "(client_id, claim_kind, claim_key)"
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_doc_types '
            "DROP COLUMN IF EXISTS claim_scope_keys"
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claim_review_configs '
            "DROP COLUMN IF EXISTS scope_code"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for schema in _postgres_schemas(bind):
            _downgrade_postgres(bind, schema)
        return
    exact_count = bind.scalar(
        sa.text("SELECT count(*) FROM claim_review_configs WHERE scope_code <> '*'")
    )
    if exact_count:
        raise RuntimeError("Cannot downgrade: exact subtype review configs exist.")
    with sqlite_fk_guard(bind):
        with op.batch_alter_table("claim_doc_types", recreate="always") as batch:
            batch.drop_column("claim_scope_keys")
        with op.batch_alter_table("claim_review_configs", recreate="always") as batch:
            batch.drop_constraint(_NEW_UQ, type_="unique")
            batch.create_unique_constraint(
                _OLD_UQ, ["client_id", "claim_kind", "claim_key"]
            )
            batch.drop_column("scope_code")
