"""Name the SP catalog product for the cover it is: outpatient specialist.

The catalog seeded code ``SP`` as "Supplementary Plan" while the product
registry, its enrolment template and every slip that uses the code describe the
specialist outpatient cover. Brokers mapping a listing's "Group Clinical
Specialist" block saw "Supplementary Plan" against SP. Only the catalog row
(``client_id IS NULL``) still carrying the old name is renamed; a company's own
product and a name a broker already changed are left alone.

Revision ID: e7a9c1d3f5b8
Revises: d5f7a9b1c3e6
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a9c1d3f5b8"
down_revision: str | None = "d5f7a9b1c3e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "Supplementary Plan"
_NEW = "Group Outpatient Specialist"


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    result: list[str | None] = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.products"}
        ):
            result.append(schema)
    return result


def _rename(old: str, new: str) -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        table = "products" if schema is None else f'"{schema}".products'
        bind.execute(
            sa.text(
                f"UPDATE {table} SET display_name = :new "
                "WHERE code = 'SP' AND client_id IS NULL AND display_name = :old"
            ),
            {"old": old, "new": new},
        )


def upgrade() -> None:
    _rename(_OLD, _NEW)


def downgrade() -> None:
    _rename(_NEW, _OLD)
