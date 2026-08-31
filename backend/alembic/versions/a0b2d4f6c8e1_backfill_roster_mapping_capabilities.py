"""backfill roster mapping capabilities and canonical roster attributes

Revision ID: a0b2d4f6c8e1
Revises: f9a1c3e5b7d2
Create Date: 2026-08-31

Data-only companion to the preceding expand migration.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a0b2d4f6c8e1"
down_revision: str | None = "f9a1c3e5b7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ATTRIBUTES = (
    (
        "25fb204d-58e0-44ac-9086-c3255d08c001",
        "category",
        "Employee Category (roster)",
        "Classification supplied in the employee listing.",
    ),
    (
        "25fb204d-58e0-44ac-9086-c3255d08c002",
        "division",
        "Division",
        "Organisational division supplied in the employee listing.",
    ),
    (
        "25fb204d-58e0-44ac-9086-c3255d08c003",
        "department",
        "Department",
        "Organisational department supplied in the employee listing.",
    ),
    (
        "25fb204d-58e0-44ac-9086-c3255d08c004",
        "cost_centre",
        "Cost Centre",
        "Cost centre supplied in the employee listing.",
    ),
)


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    result: list[str | None] = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.employee_attribute_schemas"},
        ):
            result.append(schema)
    return result


def _table(schema: str | None) -> str:
    if schema is None:
        return "employee_attribute_schemas"
    return f'"{schema}".employee_attribute_schemas'


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        table = _table(schema)
        bind.execute(sa.text(
            f"UPDATE {table} SET allow_ai_values = CASE WHEN is_pii THEN FALSE ELSE TRUE END"
        ))
        labels = {
            "grade": "Hay Grade (derived)",
            "job_grade": "Job Grade (roster)",
            "job_category": "Job Category Code (derived)",
            "role": "Executive Role (derived)",
        }
        for attribute_id, display_name in labels.items():
            bind.execute(sa.text(
                f"UPDATE {table} SET display_name = :display_name "
                "WHERE client_id IS NULL AND attribute_id = :attribute_id"
            ), {"attribute_id": attribute_id, "display_name": display_name})
        bind.execute(sa.text(
            f"UPDATE {table} SET derived_from = NULL, derivation_rule = NULL "
            "WHERE client_id IS NULL AND attribute_id = 'pass'"
        ))
        # These four columns are already parsed and stored. Giving them schema
        # rows makes them selectable and measurable without renaming any data.
        for row_id, attribute_id, display_name, description in _ATTRIBUTES:
            exists = bind.scalar(sa.text(
                f"SELECT 1 FROM {table} WHERE client_id IS NULL AND attribute_id = :attribute_id"
            ), {"attribute_id": attribute_id})
            if exists:
                continue
            bind.execute(sa.text(
                f"INSERT INTO {table} "
                "(id, client_id, attribute_id, display_name, data_type, enum_values, "
                "is_required, is_pii, allow_matching, allow_ai_values, description, "
                "derived_from, derivation_rule, created_at, updated_at) VALUES "
                "(:id, NULL, :attribute_id, :display_name, 'string', NULL, FALSE, FALSE, "
                "TRUE, TRUE, :description, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {
                "id": row_id,
                "attribute_id": attribute_id,
                "display_name": display_name,
                "description": description,
            })


def downgrade() -> None:
    # Capability choices and newly visible schema definitions may be edited by
    # brokers after deployment. A data downgrade must not erase those choices.
    pass
