"""backfill canonical schedules onto provisional placement-slip plans

Revision ID: b4e6a8c0d2f1
Revises: a3d4e5f6b7c8
Create Date: 2026-08-30

The placement-slip ingest originally wrote each parsed plan directly, before
the shared Schedule-of-Benefits column model was built. Merged workbook cells
therefore survived only on the first plan: later plans retained qualifier labels
with null values and lost other inherited schedule content. The guided setup
held the complete canonical projection, but employee coverage reads Plan rows.

This data-only migration repairs untouched, unreviewed slip-generated plans from
their placement-slip setup draft. Confirmed and human-modified plans are never
selected. It is idempotent and deliberately irreversible: restoring incomplete
benefit schedules would re-introduce incorrect member information.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6a8c0d2f1"
down_revision: str | None = "a3d4e5f6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_BENEFIT_ITEMS = 200


def _as_dict(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _clean_value(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def _clean_limits(raw: object) -> list[dict[str, str | None]]:
    if not isinstance(raw, list):
        return []
    limits: list[dict[str, str | None]] = []
    for value in raw:
        if not isinstance(value, dict):
            continue
        label = str(value.get("label") or "").strip()
        if label:
            limits.append({"label": label, "value": _clean_value(value.get("value"))})
    return limits


def _column_id(sob: dict[str, Any], plan_code: str) -> str | None:
    columns = sob.get("columns")
    if not isinstance(columns, list):
        return None
    for column in columns:
        if not isinstance(column, dict):
            continue
        if plan_code in (column.get("plan_codes") or []):
            value = column.get("id")
            return value if isinstance(value, str) else None
    if len(columns) == 1 and isinstance(columns[0], dict):
        value = columns[0].get("id")
        return value if isinstance(value, str) else None
    return None


def _effective(overrides: object, column_id: str | None, base: object) -> object:
    if column_id is None or not isinstance(overrides, dict):
        return base
    override = overrides.get(column_id)
    return base if override is None else override


def _project_schedule(answers: dict[str, Any], plan_code: str) -> dict[str, Any]:
    sob = answers.get("sob")
    if not isinstance(sob, dict):
        return {"items": []}
    items = sob.get("items")
    if not isinstance(items, list):
        return {"items": []}
    column_id = _column_id(sob, plan_code)
    if column_id is None and sob.get("columns"):
        return {"items": []}

    projected: list[dict[str, Any]] = []
    for item in items[:_MAX_BENEFIT_ITEMS]:
        if not isinstance(item, dict):
            continue
        properties = {
            str(key): str(value)
            for key, value in (item.get("properties") or {}).items()
        } if isinstance(item.get("properties"), dict) else {}
        column_properties = item.get("column_properties")
        if isinstance(column_properties, dict) and column_id:
            values = column_properties.get(column_id)
            if isinstance(values, dict):
                properties.update({str(key): str(value) for key, value in values.items()})

        sub_items: list[dict[str, Any]] = []
        raw_sub_items = item.get("sub_items")
        for sub_item in raw_sub_items if isinstance(raw_sub_items, list) else []:
            if not isinstance(sub_item, dict):
                continue
            sub_items.append(
                {
                    "key": str(sub_item.get("key") or ""),
                    "name": str(sub_item.get("name") or ""),
                    "value": _clean_value(
                        _effective(
                            sub_item.get("overrides"),
                            column_id,
                            sub_item.get("base_value"),
                        )
                    ),
                    "note": _clean_value(sub_item.get("note")),
                    "limits": _clean_limits(sub_item.get("limits")),
                    "kind": _clean_value(sub_item.get("kind")),
                }
            )
        projected.append(
            {
                "number": str(item.get("number") or ""),
                "name": str(item.get("name") or ""),
                "value": _clean_value(
                    _effective(item.get("overrides"), column_id, item.get("base_value"))
                ),
                "note": _clean_value(item.get("note")),
                "limits": _clean_limits(item.get("limits")),
                "sub_items": sub_items,
                "properties": properties,
                "kind": _clean_value(item.get("kind")),
            }
        )
    return {"items": projected}


def _normalized_identity(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _item_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    number = _normalized_identity(item.get("number"))
    name = _normalized_identity(item.get("name"))
    if number and name:
        return ("number_name", number, name)
    if name:
        return ("name", name)
    if number:
        return ("number", number)
    return None


def _sub_item_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    key = _normalized_identity(item.get("key"))
    if key:
        return ("key", key)
    name = _normalized_identity(item.get("name"))
    return ("name", name) if name else None


def _limit_identity(item: dict[str, Any]) -> tuple[str, ...] | None:
    label = _normalized_identity(item.get("label"))
    return ("label", label) if label else None


def _has_projected_value(value: object) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _merge_schedule_entry(
    existing: dict[str, Any], projected: dict[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(existing)
    for key, value in projected.items():
        if key == "limits":
            merged[key] = _merge_schedule_rows(
                existing.get(key), value, _limit_identity
            )
        elif key == "sub_items":
            merged[key] = _merge_schedule_rows(
                existing.get(key), value, _sub_item_identity
            )
        elif key == "properties" and isinstance(value, dict):
            properties = existing.get(key)
            merged[key] = {
                **(properties if isinstance(properties, dict) else {}),
                **value,
            }
        elif _has_projected_value(value) or key not in merged:
            merged[key] = deepcopy(value)
    return merged


def _merge_schedule_rows(
    existing: object,
    projected: object,
    identity: Any,
) -> list[Any]:
    existing_rows = existing if isinstance(existing, list) else []
    projected_rows = projected if isinstance(projected, list) else []
    consumed: set[int] = set()
    merged: list[Any] = []

    for projected_row in projected_rows:
        if not isinstance(projected_row, dict):
            merged.append(deepcopy(projected_row))
            continue
        projected_key = identity(projected_row)
        match_index = next(
            (
                index
                for index, existing_row in enumerate(existing_rows)
                if index not in consumed
                and isinstance(existing_row, dict)
                and projected_key is not None
                and identity(existing_row) == projected_key
            ),
            None,
        )
        if match_index is None:
            merged.append(deepcopy(projected_row))
            continue
        consumed.add(match_index)
        merged.append(
            _merge_schedule_entry(existing_rows[match_index], projected_row)
        )

    merged.extend(
        deepcopy(row)
        for index, row in enumerate(existing_rows)
        if index not in consumed
    )
    return merged


def _merge_schedule(
    existing: dict[str, Any] | None, projected: dict[str, Any]
) -> dict[str, Any]:
    existing_schedule = existing or {}
    merged = deepcopy(existing_schedule)
    for key, value in projected.items():
        if key == "items":
            merged[key] = _merge_schedule_rows(
                existing_schedule.get(key), value, _item_identity
            )
        elif _has_projected_value(value) or key not in merged:
            merged[key] = deepcopy(value)
    return merged


def _schemas(connection: sa.engine.Connection) -> list[str | None]:
    if connection.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    firm_ids = connection.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if connection.scalar(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.product_setups"},
        ):
            schemas.append(schema)
    return schemas


def _schema_prefix(connection: sa.engine.Connection, schema: str | None) -> str:
    if schema is None:
        return ""
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    return f"{quoted}."


def _plans_table(schema: str | None) -> sa.TableClause:
    return sa.table(
        "plans",
        sa.column("id", sa.String),
        sa.column("benefit_schedule", sa.JSON),
        schema=schema,
    )


def upgrade() -> None:
    connection = op.get_bind()
    updated = 0
    considered = 0
    for schema in _schemas(connection):
        prefix = _schema_prefix(connection, schema)
        setups = connection.execute(
            sa.text(
                "SELECT policy_year_id, product_code, answers "
                f"FROM {prefix}product_setups "
                "WHERE status = 'draft' AND origin = 'placement_slip'"
            )
        ).fetchall()
        plans_table = _plans_table(schema)
        for policy_year_id, product_code, raw_answers in setups:
            answers = _as_dict(raw_answers)
            if not answers:
                continue
            raw_plans = answers.get("plans")
            if not isinstance(raw_plans, list):
                continue
            selected_codes = {
                str(plan.get("code") or "").strip()
                for plan in raw_plans
                if isinstance(plan, dict)
                and plan.get("selected")
                and str(plan.get("code") or "").strip()
            }
            if not selected_codes:
                continue

            plans = connection.execute(
                sa.text(
                    "SELECT plan.id, plan.code, plan.benefit_schedule "
                    f"FROM {prefix}plans AS plan "
                    f"JOIN {prefix}products AS product ON product.id = plan.product_id "
                    "WHERE plan.policy_year_id = :policy_year_id "
                    "AND upper(product.code) = :product_code "
                    "AND plan.source = 'system_generated' "
                    "AND plan.status = 'needs_review' "
                    "AND plan.human_modified = false"
                ),
                {
                    "policy_year_id": policy_year_id,
                    "product_code": str(product_code or "").upper(),
                },
            ).fetchall()
            for plan_id, plan_code, raw_schedule in plans:
                code = str(plan_code or "").strip()
                if code not in selected_codes:
                    continue
                considered += 1
                schedule = _project_schedule(answers, code)
                # A mapping gap must not erase a real parsed schedule during
                # repair. Confirmation validates and resolves that gap.
                if not schedule["items"]:
                    continue
                merged = _merge_schedule(_as_dict(raw_schedule), schedule)
                if merged == _as_dict(raw_schedule):
                    continue
                connection.execute(
                    plans_table.update()
                    .where(plans_table.c.id == plan_id)
                    .values(benefit_schedule=merged)
                )
                updated += 1

    print(
        "[backfill_provisional_plan_schedules] "
        f"updated {updated}/{considered} eligible plan schedules"
    )


def downgrade() -> None:
    # Data-only and intentionally irreversible. A forward repair is the safe
    # rollback for any incorrectly projected record.
    pass
