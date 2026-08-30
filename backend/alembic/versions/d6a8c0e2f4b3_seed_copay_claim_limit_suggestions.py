"""seed annual claim-limit suggestions from structured copay fields

Revision ID: d6a8c0e2f4b3
Revises: c5f7b9d1e3a2
Create Date: 2026-08-30

Outpatient schedules store their per-policy-year amount in the copay
``properties`` bag rather than the row's flat ``value``.  The first structured
limit backfill therefore missed those rows.  This data-only follow-up adds
``needs_review`` suggestions without overwriting any existing broker decision.
It updates public and every provisioned firm schema and is idempotent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "d6a8c0e2f4b3"
down_revision: str | None = "c5f7b9d1e3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_EMPTY_VALUES = {"na", "n/a", "not applicable", "not covered"}


def _as_dict(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _amount(value: str) -> float | None:
    match = _AMOUNT_RE.search(value)
    if match is None:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return amount if amount >= 0 else None


def _setting(value: object, scopes: list[str]) -> dict[str, Any] | None:
    text = " ".join(str(value or "").split())
    if not text or text.casefold() in _EMPTY_VALUES:
        return None
    folded = text.casefold()
    if "as charged" in folded:
        basis, amount = "as_charged", None
    else:
        basis, amount = "policy_year", _amount(text)
    display = text if "year" in folded else f"{text} per policy year"
    return {
        "basis": basis,
        "amount": amount,
        "currency": "SGD",
        "display": display,
        "claim_scope_codes": scopes,
        "status": "needs_review",
        "source": "detected",
    }


def _scopes(product_code: object, row_name: object) -> list[str]:
    code = str(product_code or "").strip().upper()
    name = " ".join(str(row_name or "").split()).casefold()
    if code in {"GP", "GCGP", "GOGP"}:
        if any(word in name for word in ("tcm", "traditional chinese", "chinese physician")):
            return ["gp_tcm"]
        if "physio" in name:
            return ["gp_physiotherapy"]
        if any(word in name for word in ("general practitioner", "outpatient gp", "gp consult")):
            return ["standard"]
    return []


def _plan_source(item: dict[str, Any]) -> object:
    properties = item.get("properties")
    return properties.get("per_policy_year") if isinstance(properties, dict) else None


def _setup_source(item: dict[str, Any], column_id: str) -> object:
    column_properties = item.get("column_properties")
    per_column = (
        column_properties.get(column_id)
        if isinstance(column_properties, dict)
        else None
    )
    if isinstance(per_column, dict) and per_column.get("per_policy_year") is not None:
        return per_column.get("per_policy_year")
    properties = item.get("properties")
    return properties.get("per_policy_year") if isinstance(properties, dict) else None


def _seed_plan(schedule: dict[str, Any], product_code: str) -> bool:
    before = deepcopy(schedule)
    assigned: set[str] = set()
    for item in schedule.get("items") or []:
        if not isinstance(item, dict):
            continue
        current = item.get("claim_limit")
        if isinstance(current, dict):
            assigned.update(current.get("claim_scope_codes") or [])
            continue
        scopes = [
            scope
            for scope in _scopes(product_code, item.get("name"))
            if scope not in assigned
        ]
        if setting := _setting(_plan_source(item), scopes):
            item["claim_limit"] = setting
            assigned.update(scopes)
    return schedule != before


def _seed_setup(sob: dict[str, Any], product_code: str) -> bool:
    before = deepcopy(sob)
    columns = [column for column in (sob.get("columns") or []) if isinstance(column, dict)]
    assigned: dict[str, set[str]] = {
        str(column.get("id") or ""): set()
        for column in columns
        if column.get("id")
    }
    for item in sob.get("items") or []:
        if not isinstance(item, dict):
            continue
        current_limits = item.get("claim_limits")
        limits = dict(current_limits) if isinstance(current_limits, dict) else {}
        suggested_scopes = _scopes(product_code, item.get("name"))
        for column in columns:
            column_id = str(column.get("id") or "")
            if not column_id:
                continue
            current = limits.get(column_id)
            if isinstance(current, dict):
                assigned[column_id].update(current.get("claim_scope_codes") or [])
                continue
            scopes = [
                scope for scope in suggested_scopes if scope not in assigned[column_id]
            ]
            if setting := _setting(_setup_source(item, column_id), scopes):
                limits[column_id] = setting
                assigned[column_id].update(scopes)
        if limits:
            item["claim_limits"] = limits
    return sob != before


def _schemas(connection: sa.engine.Connection) -> list[str | None]:
    if connection.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for firm_id in connection.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if connection.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.product_setups"},
        ):
            schemas.append(schema)
    return schemas


def _prefix(connection: sa.engine.Connection, schema: str | None) -> str:
    if schema is None:
        return ""
    return connection.dialect.identifier_preparer.quote_schema(schema) + "."


def _json_table(name: str, schema: str | None, json_column: str) -> sa.TableClause:
    return sa.table(
        name,
        sa.column("id", sa.String),
        sa.column(json_column, sa.JSON),
        schema=schema,
    )


def upgrade() -> None:
    connection = op.get_bind()
    plan_updates = setup_updates = 0
    for schema in _schemas(connection):
        prefix = _prefix(connection, schema)
        plans_table = _json_table("plans", schema, "benefit_schedule")
        setups_table = _json_table("product_setups", schema, "answers")
        plans = connection.execute(
            sa.text(
                "SELECT plan.id, product.code, plan.benefit_schedule "
                f"FROM {prefix}plans AS plan JOIN {prefix}products AS product "
                "ON product.id = plan.product_id"
            )
        ).fetchall()
        for plan_id, product_code, raw_schedule in plans:
            schedule = _as_dict(raw_schedule)
            if schedule is not None and _seed_plan(schedule, str(product_code or "")):
                connection.execute(
                    plans_table.update()
                    .where(plans_table.c.id == plan_id)
                    .values(benefit_schedule=schedule)
                )
                plan_updates += 1

        setups = connection.execute(
            sa.text(f"SELECT id, product_code, answers FROM {prefix}product_setups")
        ).fetchall()
        for setup_id, product_code, raw_answers in setups:
            answers = _as_dict(raw_answers)
            sob = answers.get("sob") if answers else None
            if not isinstance(answers, dict) or not isinstance(sob, dict):
                continue
            if _seed_setup(sob, str(product_code or "")):
                connection.execute(
                    setups_table.update()
                    .where(setups_table.c.id == setup_id)
                    .values(answers=answers)
                )
                setup_updates += 1
    print(f"[seed_copay_claim_limits] plans={plan_updates} setups={setup_updates}")


def downgrade() -> None:
    # Intentionally irreversible: suggestions are indistinguishable from ones
    # a broker may have reviewed after this migration ran. A forward migration
    # is the safe rollback path.
    pass
