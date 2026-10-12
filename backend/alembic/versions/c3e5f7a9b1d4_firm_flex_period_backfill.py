"""Restore Flex on firm-schema enrolment periods that predate the uses_flex flag.

Revision ID: c3e5f7a9b1d4
Revises: b2d4f6a8c0e3
Create Date: 2026-10-11

a3d4e5f6b7c8 added ``enrollment_windows.uses_flex`` and kept Flex on for every
period with a saved Flex price tag or leave flex amount. Its UPDATE was not
schema-qualified, so on PostgreSQL it reached ``public`` only. The firm schemas,
where the periods live, got the column from ``sync_firm_schema`` with the model
default ``false`` and no backfill: periods that had run as Flex lost their
wallets and price tags and skip the wallet and Flex readiness checks.

This applies the same evidence rule in each firm schema, to rows that still hold
that provisioning default and nothing else:

- the period was created before the flag existed (``_FLAG_INTRODUCED``, the
  commit of a3d4e5f6b7c8), so nobody chose the value when creating it; and
- no audit entry for the period carries ``uses_flex``. The period PATCH is the
  only writer of the flag on an existing period, and it audits every field it
  was sent (the draft form always sends this one), so any explicit later
  choice, true or false, is kept.

A firm schema still without the column (a database upgraded from before
a3d4e5f6b7c8 whose firms have not been provisioned since) gets it here. Every
period there predates the flag, so the original rule applies unrestricted and
provisioning no longer adds the column with ``false``.

SQLite and ``public`` were covered by a3d4e5f6b7c8. Idempotent. Downgrade keeps
the data: ``false`` was never anyone's choice for the repaired rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "c3e5f7a9b1d4"
down_revision: str | None = "b2d4f6a8c0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Commit time of a3d4e5f6b7c8. No release could set the flag on a period before.
_FLAG_INTRODUCED = "2026-08-29T03:16:56+00:00"
_EVIDENCE = (("enrollment_elections", "flex_price_tag"), ("leave_elections", "flex_amount"))


def _firm_schemas(bind: sa.engine.Connection) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    schemas = [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    ]
    return [
        schema
        for schema in schemas
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f'"{schema}".enrollment_windows'},
        )
    ]


def _columns(bind: sa.engine.Connection, schema: str, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table, schema=schema):
        return set()
    return {column["name"] for column in inspector.get_columns(table, schema=schema)}


def _flex_evidence(bind: sa.engine.Connection, schema: str) -> str | None:
    """Periods with a saved Flex coverage or leave amount (a3d4e5f6b7c8's rule)."""
    if "window_id" not in _columns(bind, schema, "enrollments"):
        return None
    sources = [
        f'SELECT enrollment.window_id FROM "{schema}".enrollments AS enrollment '
        f'JOIN "{schema}".{table} AS saved ON saved.enrollment_id = enrollment.id '
        f"WHERE saved.{column} IS NOT NULL"
        for table, column in _EVIDENCE
        if column in _columns(bind, schema, table)
    ]
    return " UNION ".join(sources) or None


def _chosen_by_a_user(schema: str) -> str:
    return " OR ".join(
        f"EXISTS (SELECT 1 FROM {audit} AS audit "
        "WHERE audit.entity_type = 'enrollment_window' AND audit.entity_id = period.id "
        "AND (audit.after::jsonb -> 'uses_flex') IS NOT NULL)"
        for audit in (f'"{schema}".audit_log', "public.audit_log")
    )


def _restore(bind: sa.engine.Connection, schema: str, evidence: str, *, all_rows: bool) -> int:
    where = f"period.uses_flex = false AND period.id IN ({evidence})"
    params: dict[str, Any] = {}
    if not all_rows:
        where += (
            " AND period.created_at < CAST(:introduced AS timestamptz)"
            f" AND NOT ({_chosen_by_a_user(schema)})"
        )
        params["introduced"] = _FLAG_INTRODUCED
    result = bind.execute(
        sa.text(
            f'UPDATE "{schema}".enrollment_windows AS period SET uses_flex = true WHERE {where}'
        ),
        params,
    )
    return int(result.rowcount or 0)


def _left_off(bind: sa.engine.Connection, schema: str, evidence: str) -> int:
    return int(
        bind.scalar(
            sa.text(
                f'SELECT count(*) FROM "{schema}".enrollment_windows AS period '
                f"WHERE period.uses_flex = false AND period.id IN ({evidence})"
            )
        )
        or 0
    )


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _firm_schemas(bind):
        all_rows = "uses_flex" not in _columns(bind, schema, "enrollment_windows")
        if all_rows:
            op.add_column(
                "enrollment_windows",
                sa.Column("uses_flex", sa.Boolean(), nullable=False, server_default=sa.false()),
                schema=schema,
            )
        elif "after" not in _columns(bind, schema, "audit_log"):
            print(f"[firm_flex_period_backfill] {schema}: no audit trail; left unchanged")
            continue
        evidence = _flex_evidence(bind, schema)
        if evidence is None:
            continue
        restored = _restore(bind, schema, evidence, all_rows=all_rows)
        print(
            f"[firm_flex_period_backfill] {schema}: restored={restored} "
            f"kept_off={_left_off(bind, schema, evidence)}"
        )


def downgrade() -> None:
    # Data only: turning Flex back off would re-break the repaired periods.
    pass
