"""Repair constraints earlier migrations applied to `public` but not to firm schemas.

Revision ID: a1c3e5f7b9d2
Revises: f3b5d7e9a1c4
Create Date: 2026-10-08

The first schema drift check against production (deploy f0db616) found three
constraints that exist on the models, and on schemas provisioned from them,
but that firm schemas evolved by migration never received:

- ``claim_messages.enquiry_id`` → ``member_enquiries.id`` (ON DELETE CASCADE):
  ``a3f7c9d21b48`` created the foreign key on the unqualified table only.
- ``underwriting_cases.review_id`` → ``underwriting_reviews.id`` (ON DELETE
  CASCADE): ``d8f0a2c4e6b8`` added the column without one.
- ``ix_report_versions_series`` UNIQUE: ``a3f7c2d9e614`` recreated the index as
  unique on the unqualified table only.

Each is added to every firm schema that lacks it. Rows that would violate one
stop the migration with counts — they need a deliberate decision, never a
silent delete or renumber. PostgreSQL only: SQLite has a single schema built
from the models.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: str | None = "f3b5d7e9a1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, parent table, constraint name)
_FOREIGN_KEYS = (
    ("claim_messages", "enquiry_id", "member_enquiries", "fk_claim_messages_enquiry_id"),
    ("underwriting_cases", "review_id", "underwriting_reviews", "fk_underwriting_cases_review_id"),
)
_SERIES_INDEX = "ix_report_versions_series"
_SERIES_COLUMNS = ("client_id", "policy_year_id", "report_type", "scope_key", "version_no")


def _firm_schemas(bind: sa.engine.Connection) -> list[str]:
    return [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    ]


def _has_table(bind: sa.engine.Connection, schema: str, table: str) -> bool:
    return bool(
        bind.execute(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f'"{schema}"."{table}"'}
        ).scalar()
    )


def _has_foreign_key(
    bind: sa.engine.Connection, schema: str, table: str, column: str, parent: str
) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "JOIN pg_class p ON p.oid = c.confrelid "
                "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey) "
                "WHERE c.contype = 'f' AND n.nspname = :schema AND t.relname = :table "
                "AND p.relname = :parent AND a.attname = :column"
            ),
            {"schema": schema, "table": table, "parent": parent, "column": column},
        ).first()
    )


def _add_foreign_key(
    bind: sa.engine.Connection, schema: str, table: str, column: str, parent: str, name: str
) -> None:
    if not (_has_table(bind, schema, table) and _has_table(bind, schema, parent)):
        return
    if _has_foreign_key(bind, schema, table, column, parent):
        return
    orphans = bind.execute(
        sa.text(
            f'SELECT count(*) FROM "{schema}"."{table}" child '
            f"WHERE child.{column} IS NOT NULL AND NOT EXISTS "
            f'(SELECT 1 FROM "{schema}"."{parent}" p WHERE p.id = child.{column})'
        )
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"{schema}.{table} has {orphans} row(s) whose {column} names a missing "
            f"{parent} row. Resolve them deliberately, then rerun the migration."
        )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}"."{table}" ADD CONSTRAINT "{name}" '
            f'FOREIGN KEY ({column}) REFERENCES "{schema}"."{parent}" (id) ON DELETE CASCADE'
        )
    )


def _make_series_unique(bind: sa.engine.Connection, schema: str) -> None:
    if not _has_table(bind, schema, "report_versions"):
        return
    unique = bind.execute(
        sa.text(
            "SELECT ix.indisunique FROM pg_index ix "
            "JOIN pg_class i ON i.oid = ix.indexrelid "
            "JOIN pg_namespace n ON n.oid = i.relnamespace "
            "WHERE n.nspname = :schema AND i.relname = :name"
        ),
        {"schema": schema, "name": _SERIES_INDEX},
    ).scalar()
    if unique:
        return
    columns = ", ".join(_SERIES_COLUMNS)
    duplicates = bind.execute(
        sa.text(
            f'SELECT count(*) FROM (SELECT 1 FROM "{schema}".report_versions '
            f"GROUP BY {columns} HAVING count(*) > 1) d"
        )
    ).scalar()
    if duplicates:
        raise RuntimeError(
            f"{schema}.report_versions has {duplicates} duplicated version number(s) "
            "within a series. Renumber them deliberately, then rerun the migration."
        )
    bind.execute(sa.text(f'DROP INDEX IF EXISTS "{schema}"."{_SERIES_INDEX}"'))
    bind.execute(
        sa.text(
            f'CREATE UNIQUE INDEX "{_SERIES_INDEX}" ON "{schema}".report_versions ({columns})'
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for schema in _firm_schemas(bind):
        for table, column, parent, name in _FOREIGN_KEYS:
            _add_foreign_key(bind, schema, table, column, parent, name)
        _make_series_unique(bind, schema)


def downgrade() -> None:
    # The repaired constraints are what the models declare; removing them would
    # only reintroduce the drift. Nothing to undo.
    pass
