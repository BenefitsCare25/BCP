"""Move member records left on a catalog product onto the company's own.

Confirming a slip-origin setup re-parents the year's categories from the
catalog product to the company's product of the same code. Until now it left
the year's member records behind: the uploaded Employee Listing's rows,
elections, plan overrides and underwriting cases still named the catalog
product, so matching, headcounts and the Full EL no longer found them for the
company's plans. The setup now moves them too; this repairs records stranded
before that.

A record is moved only when its product has no category that year and exactly
one same-code product does, and the move collides with no record already on
that product. Anything else is left as it is. One-way: the old product id is
not kept, and moving records back would strand them again.

Revision ID: d9e1f3a5c7b2
Revises: f2c4e6a8b0d1
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e1f3a5c7b2"
down_revision: str | None = "f2c4e6a8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table -> the columns (besides product_id) that identify one record, so a
# move never creates a second record for the same member and product.
_RECORDS: dict[str, tuple[str, ...]] = {
    "listing_assignments": ("policy_year_id", "member_key"),
    "employee_plan_overrides": ("employee_id",),
    "enrollment_elections": ("enrollment_id",),
    "underwriting_cases": ("policy_year_id", "employee_id", "dependant_id"),
}


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    result: list[str | None] = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        result.append("firm_" + "".join(char for char in str(firm_id) if char.isalnum()))
    return result


def _table(schema: str | None, name: str) -> str:
    return name if schema is None else f'"{schema}".{name}'


def _exists(bind: sa.engine.Connection, schema: str | None, name: str) -> bool:
    if schema is None:
        return sa.inspect(bind).has_table(name)
    return bool(
        bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.{name}"}
        )
    )


def _category_products(
    bind: sa.engine.Connection, schema: str | None
) -> dict[tuple[str, str], set[str]]:
    """(policy year, product code) -> products holding its categories."""
    out: dict[tuple[str, str], set[str]] = defaultdict(set)
    for year, code, product_id in bind.execute(
        sa.text(
            f"SELECT c.policy_year_id, UPPER(p.code), c.product_id "
            f"FROM {_table(schema, 'categories')} c "
            f"JOIN {_table(schema, 'products')} p ON p.id = c.product_id"
        )
    ):
        out[(year, code)].add(product_id)
    return out


def _move(
    bind: sa.engine.Connection,
    schema: str | None,
    name: str,
    keys: tuple[str, ...],
    targets: dict[tuple[str, str], set[str]],
) -> None:
    table = _table(schema, name)
    columns = ", ".join(f"r.{k}" for k in keys)
    rows = bind.execute(
        sa.text(
            f"SELECT r.id, r.policy_year_id, UPPER(p.code), r.product_id, {columns} "
            f"FROM {table} r JOIN {_table(schema, 'products')} p ON p.id = r.product_id"
        )
    ).all()
    held = {(row[3], *row[4:]) for row in rows}
    for row in rows:
        record_id, year, code, product_id, *identity = row
        owners = targets.get((year, code), set())
        if product_id in owners or len(owners) != 1:
            continue
        target = next(iter(owners))
        if (target, *identity) in held:
            continue
        bind.execute(
            sa.text(f"UPDATE {table} SET product_id = :target WHERE id = :id"),
            {"target": target, "id": record_id},
        )
        held.discard((product_id, *identity))
        held.add((target, *identity))


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        if not _exists(bind, schema, "categories"):
            continue
        targets = _category_products(bind, schema)
        for name, keys in _RECORDS.items():
            if _exists(bind, schema, name):
                _move(bind, schema, name, keys, targets)


def downgrade() -> None:
    pass
