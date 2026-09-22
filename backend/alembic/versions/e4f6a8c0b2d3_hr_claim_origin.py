"""add delegated HR as a first-class claim origin

Revision ID: e4f6a8c0b2d3
Revises: d3e5f7a9b1c2
Create Date: 2026-09-22

The claim table exists in public for SQLite/local operation and in every firm
schema on PostgreSQL. Both locations must accept the same origin vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4f6a8c0b2d3"
down_revision: str | None = "d3e5f7a9b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_claims_origin_valid"
_BEFORE = "origin IN ('portal', 'broker')"
_AFTER = "origin IN ('portal', 'broker', 'hr')"


def _firm_schemas(bind: sa.engine.Connection) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    return [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(
            sa.text("SELECT id FROM public.broker_firms")
        ).scalars()
    ]


def _postgres_claim_schemas(bind: sa.engine.Connection) -> list[str]:
    schemas = ["public", *_firm_schemas(bind)]
    return [
        schema
        for schema in schemas
        if bind.scalar(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.claims"},
        )
    ]


def _replace_postgres_constraint(
    bind: sa.engine.Connection,
    schema: str,
    condition: str,
) -> None:
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claims '
            f'DROP CONSTRAINT IF EXISTS "{_NAME}", '
            f'ADD CONSTRAINT "{_NAME}" CHECK ({condition}) NOT VALID'
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".claims '
            f'VALIDATE CONSTRAINT "{_NAME}"'
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("claims", recreate="always") as batch:
            batch.drop_constraint(_NAME, type_="check")
            batch.create_check_constraint(_NAME, _AFTER)
        return
    for schema in _postgres_claim_schemas(bind):
        _replace_postgres_constraint(bind, schema, _AFTER)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The provenance snapshot remains in intake_meta, but the old schema has
        # no HR origin value. Keep delegated claims internal-only on rollback by
        # mapping them to broker rather than exposing them in the member portal.
        op.execute(sa.text("UPDATE claims SET origin = 'broker' WHERE origin = 'hr'"))
        with op.batch_alter_table("claims", recreate="always") as batch:
            batch.drop_constraint(_NAME, type_="check")
            batch.create_check_constraint(_NAME, _BEFORE)
        return
    for schema in _postgres_claim_schemas(bind):
        bind.execute(
            sa.text(
                f'UPDATE "{schema}".claims SET origin = \'broker\' '
                "WHERE origin = 'hr'"
            )
        )
        _replace_postgres_constraint(bind, schema, _BEFORE)
