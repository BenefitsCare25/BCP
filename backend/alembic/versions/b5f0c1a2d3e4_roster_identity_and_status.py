"""Roster identity (normalized NRIC) + soft-termination columns

Adds, to both ``employees`` and ``dependants``:
- ``national_id_normalized`` (indexed, nullable) — canonical NRIC/FIN used as
  the person dedup key and for ADC record resolution.
- ``terminated_effective`` (nullable Date) — effective date of an ADC soft
  deletion; the row's ``status`` becomes ``terminated`` but history is kept.

Additive + nullable — back-compatible. Backfills ``national_id_normalized``
from the existing ``attribute_values`` JSON blob (employee ``id_no`` /
dependant ``dependant_id_no``) so pre-existing rosters are dedup-comparable
immediately. Cross-dialect: the blob arrives as a dict (Postgres jsonb) or a
JSON string (SQLite text), handled in Python rather than via SQL JSON ops.

Revision ID: b5f0c1a2d3e4
Revises: b4d8e2f6a1c3
Create Date: 2026-07-15 00:00:00.000000

"""
import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5f0c1a2d3e4"
down_revision: Union[str, None] = "b4d8e2f6a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize(raw: object | None) -> str | None:
    """Mirror of roster_attributes.normalize_nric (inlined so the migration is
    self-contained and won't drift if the app helper is later refactored)."""
    if raw is None:
        return None
    canon = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
    return canon or None


def _blob(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _backfill(table: str, id_keys: tuple[str, ...]) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(f"SELECT id, attribute_values FROM {table}")
    ).fetchall()
    updates: list[dict[str, str]] = []
    for row_id, attrs in rows:
        blob = _blob(attrs)
        nric = None
        for key in id_keys:
            v = blob.get(key)
            if v not in (None, ""):
                nric = _normalize(v)
                if nric:
                    break
        if nric:
            updates.append({"nid": nric, "rid": row_id})
    if updates:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET national_id_normalized = :nid WHERE id = :rid"
            ),
            updates,
        )


def upgrade() -> None:
    for table in ("employees", "dependants"):
        op.add_column(
            table,
            sa.Column("national_id_normalized", sa.String(64), nullable=True),
        )
        op.add_column(
            table, sa.Column("terminated_effective", sa.Date(), nullable=True)
        )
        op.create_index(
            f"ix_{table}_national_id_normalized",
            table,
            ["national_id_normalized"],
        )

    _backfill("employees", ("id_no", "nric", "fin"))
    _backfill("dependants", ("dependant_id_no", "id_no", "nric", "fin"))


def downgrade() -> None:
    for table in ("employees", "dependants"):
        op.drop_index(f"ix_{table}_national_id_normalized", table_name=table)
        op.drop_column(table, "terminated_effective")
        op.drop_column(table, "national_id_normalized")
