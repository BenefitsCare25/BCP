"""Name the plans a placement slip did not name: "Plan 1", with its details.

Slips for sum-assured products (GTL, GPA, GCI, WICA) often have no plan column,
and the parser named such plans after the section heading ("Schedule of
Benefits") or a category's wording ("Directors"). Uploads now name them by
code and store their categories' basis as the details ("24x BMS"); see
``slip_parsing.plan_names``. This applies the same rule to plans already
stored, and to the plan labels of their setup drafts, which confirming a setup
writes back onto the plan.

Only a heading or a category's own wording is replaced; any other name is
kept. Details fill an empty report label only. Public and provisioned firm
schemas; idempotent; forward-only.

Revision ID: f2c4e6a8b0d1
Revises: e7a9c1d3f5b8
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "f2c4e6a8b0d1"
down_revision: str | None = "e7a9c1d3f5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _as_dict(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _schemas(connection: sa.engine.Connection) -> list[str | None]:
    if connection.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for firm_id in connection.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if connection.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.plans"}
        ):
            schemas.append(schema)
    return schemas


def _prefix(connection: sa.engine.Connection, schema: str | None) -> str:
    if schema is None:
        return ""
    return connection.dialect.identifier_preparer.quote_schema(schema) + "."


def _rename_plans(
    connection: sa.engine.Connection, schema: str | None
) -> dict[tuple[str, str, str], tuple[str, str]]:
    """Rename and describe the schema's plans. Returns ``(policy year, product
    code, plan code) -> (old name, new name)`` for each renamed plan."""
    from app.services.slip_parsing.plan_names import (
        describing_bases,
        generated_name,
        plan_details,
    )

    prefix = _prefix(connection, schema)
    cited: dict[tuple[str, str, str], list[tuple[list[str], object, object]]] = defaultdict(list)
    for product_id, year_id, name, raw, assignments in connection.execute(sa.text(
        "SELECT product_id, policy_year_id, display_name, raw_description, plan_assignments "
        f"FROM {prefix}categories WHERE product_id IS NOT NULL"
    )):
        pa = _as_dict(assignments)
        code = str(pa.get("plan_code") or "").strip()
        if code:
            cited[(year_id, product_id, code)].append(
                ([name or "", raw or ""], pa.get("basis"), pa.get("member_scope"))
            )

    plans = sa.table(
        "plans",
        sa.column("id", sa.String),
        sa.column("display_name", sa.String),
        sa.column("report_label", sa.String),
        schema=schema,
    )
    renamed: dict[tuple[str, str, str], tuple[str, str]] = {}
    for plan_id, year_id, product_id, product_code, code, name, label in connection.execute(
        sa.text(
            "SELECT p.id, p.policy_year_id, p.product_id, pr.code, p.code, p.display_name, "
            f"p.report_label FROM {prefix}plans p JOIN {prefix}products pr "
            "ON pr.id = p.product_id"
        )
    ):
        own = cited.get((year_id, product_id, str(code).strip()), [])
        texts = [text for texts_, _, _ in own for text in texts_]
        bases = describing_bases((basis, scope) for _, basis, scope in own)
        new_name = generated_name(str(code), name, texts, bases)[:255]
        new_label = label or plan_details(bases)
        if new_name == name and new_label == label:
            continue
        connection.execute(
            plans.update().where(plans.c.id == plan_id).values(
                display_name=new_name, report_label=new_label
            )
        )
        if new_name != name:
            renamed[(year_id, product_code, str(code))] = (name, new_name)
    return renamed


def _relabel_setups(
    connection: sa.engine.Connection,
    schema: str | None,
    renamed: dict[tuple[str, str, str], tuple[str, str]],
) -> int:
    """Carry the new names onto setup drafts whose plan label is the old one."""
    if not renamed:
        return 0
    prefix = _prefix(connection, schema)
    setups = sa.table(
        "product_setups",
        sa.column("id", sa.String),
        sa.column("answers", sa.JSON),
        schema=schema,
    )
    years = {year_id for year_id, _, _ in renamed}
    updated = 0
    for setup_id, year_id, product_code, raw in connection.execute(sa.text(
        f"SELECT id, policy_year_id, product_code, answers FROM {prefix}product_setups"
    )):
        if year_id not in years:
            continue
        answers = _as_dict(raw)
        changed = False
        for stub in answers.get("plans") or []:
            if not isinstance(stub, dict):
                continue
            names = renamed.get((year_id, product_code, str(stub.get("code") or "")))
            if names and stub.get("label") == names[0]:
                stub["label"] = names[1]
                changed = True
        if changed:
            connection.execute(
                setups.update().where(setups.c.id == setup_id).values(answers=answers)
            )
            updated += 1
    return updated


def upgrade() -> None:
    connection = op.get_bind()
    plans = setups = 0
    for schema in _schemas(connection):
        renamed = _rename_plans(connection, schema)
        plans += len(renamed)
        setups += _relabel_setups(connection, schema, renamed)
    print(f"[name_unnamed_plans] plans_renamed={plans} setups={setups}")


def downgrade() -> None:
    # Forward-only: the old names were a section heading or a category's
    # wording, never a plan name a broker chose.
    pass
