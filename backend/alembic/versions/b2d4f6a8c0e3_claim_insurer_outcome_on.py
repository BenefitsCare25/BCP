"""Record the insurer's decline date apart from the broker's decision.

Revision ID: b2d4f6a8c0e3
Revises: a1c3e5f7b9d2
Create Date: 2026-10-11

``claims.insurer_outcome_on`` (nullable DATE): the day an insurer declined a
claim we had sent them. Until now that decline overwrote ``decided_at`` and
``decided_by``, so the servicer SLA, the register's "Decided On" and the
workload report counted the insurer's turnaround as the broker's.

Added to ``public`` (SQLite and local operation) and to every provisioned firm
schema on PostgreSQL; firms provisioned later get it from the models
(``db/tenancy.sync_firm_schema``). Existing rows stay NULL: the clock-stop
reader falls back to ``decided_at`` for declines recorded before this column.

Downgrade copies each recorded decline date back into ``decided_at`` (noon UTC,
as ``clock.stamp_for_day`` writes a stated date) before dropping the column,
because the previous release reads ``decided_at`` as the decline date and would
otherwise stop the insurer's clock on the day the broker approved.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2d4f6a8c0e3"
down_revision: str | None = "a1c3e5f7b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "claims"
_COLUMN = "insurer_outcome_on"


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    """Every location holding a `claims` table: the single SQLite schema, or
    `public` and each provisioned firm schema on PostgreSQL."""
    if bind.dialect.name != "postgresql":
        return [None]
    firm_schemas = [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    ]
    return [
        schema
        for schema in ["public", *firm_schemas]
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'"{schema}".{_TABLE}'}
        )
    ]


def _has_column(bind: sa.engine.Connection, schema: str | None) -> bool:
    columns = sa.inspect(bind).get_columns(_TABLE, schema=schema)
    return _COLUMN in {column["name"] for column in columns}


def _restore_decided_at(bind: sa.engine.Connection, schema: str | None) -> None:
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                f'UPDATE "{schema}".{_TABLE} '
                f"SET decided_at = ({_COLUMN} + TIME '12:00') AT TIME ZONE 'UTC' "
                f"WHERE {_COLUMN} IS NOT NULL"
            )
        )
        return
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET decided_at = {_COLUMN} || ' 12:00:00.000000' "
            f"WHERE {_COLUMN} IS NOT NULL"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        if not _has_column(bind, schema):
            op.add_column(_TABLE, sa.Column(_COLUMN, sa.Date(), nullable=True), schema=schema)


def downgrade() -> None:
    bind = op.get_bind()
    for schema in reversed(_schemas(bind)):
        if not _has_column(bind, schema):
            continue
        _restore_decided_at(bind, schema)
        # A plain nullable column with no index or constraint: SQLite (3.35+)
        # drops it in place, so no batch recreate of `claims` is needed.
        op.drop_column(_TABLE, _COLUMN, schema=schema)
