"""repair structured copay claim-limit suggestions

Revision ID: e8c0d2f4b6a9
Revises: d6a8c0e2f4b3
Create Date: 2026-08-30

The preceding backfill interpreted annual usage counts as SGD allowances and
could assign a scope before discovering an existing owner later in item order.
This forward-only repair changes untouched suggestions and exact one-click
verifications of those suggestions. An intentional broker override (a changed
basis or amount) is preserved. It also invalidates authoritative settings whose
stored source wording no longer matches the SoB. Public and provisioned firm
schemas are handled, and the repair is idempotent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "e8c0d2f4b6a9"
down_revision: str | None = "d6a8c0e2f4b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_MONETARY_CONTEXT_RE = re.compile(r"\$|\bsgd\b|\bdollars?\b", re.I)
_PER_YEAR_RE = re.compile(
    r"\bper\s+(?:policy\s+)?year\b|\bper\s+annum\b|/year\b", re.I
)
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


def _text(value: object) -> str:
    return " ".join(str(value or "").split())


def _amount(value: str) -> float | None:
    match = _AMOUNT_RE.search(value)
    if match is None:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return amount if amount >= 0 else None


def _display(value: object) -> str | None:
    text = _text(value)
    if not text or text.casefold() in _EMPTY_VALUES:
        return None
    return text if _PER_YEAR_RE.search(text) else f"{text} per policy year"


def _matches_inferred_setting(setting: object, source: object) -> bool:
    """Match d6 output or its one-click verification, excluding scopes."""
    if not isinstance(setting, dict):
        return False
    display = _display(source)
    if display is None:
        return False
    expected_basis = "as_charged" if "as charged" in display.casefold() else "policy_year"
    expected_amount = None if expected_basis == "as_charged" else _amount(display)
    return (
        setting.get("basis") == expected_basis
        and setting.get("amount") == expected_amount
        and setting.get("currency") == "SGD"
        and _text(setting.get("display")) == display
        and setting.get("status") in {"needs_review", "verified"}
        and setting.get("source") in {"detected", "manual"}
        and isinstance(setting.get("claim_scope_codes"), list)
    )


def _repair_setting(
    setting: dict[str, Any],
    structured_source: object,
    current_wording: str | None,
) -> tuple[dict[str, Any], bool]:
    candidate = _matches_inferred_setting(setting, structured_source)
    next_setting = deepcopy(setting)
    source_text = _text(structured_source)
    if (
        candidate
        and not _MONETARY_CONTEXT_RE.search(source_text)
        and "as charged" not in source_text.casefold()
    ):
        next_setting["basis"] = "informational"
        next_setting["amount"] = None
        next_setting["status"] = "needs_review"
    if (
        next_setting.get("status") in {"verified", "not_limit"}
        and _text(next_setting.get("display")).casefold()
        != _text(current_wording).casefold()
    ):
        next_setting["status"] = "needs_review"
    return next_setting, candidate


def _plan_source(item: dict[str, Any]) -> object:
    properties = item.get("properties")
    return properties.get("per_policy_year") if isinstance(properties, dict) else None


def _plan_wording(item: dict[str, Any]) -> str | None:
    structured = _display(_plan_source(item))
    if structured is not None:
        return structured
    value = _text(item.get("value"))
    return value or None


def _setup_source(item: dict[str, Any], column_id: str) -> object:
    column_properties = item.get("column_properties")
    per_column = (
        column_properties.get(column_id)
        if isinstance(column_properties, dict)
        else None
    )
    if isinstance(per_column, dict) and per_column.get("per_policy_year") is not None:
        return per_column.get("per_policy_year")
    return _plan_source(item)


def _setup_wording(item: dict[str, Any], column_id: str) -> str | None:
    structured = _display(_setup_source(item, column_id))
    if structured is not None:
        return structured
    overrides = item.get("overrides")
    override = overrides.get(column_id) if isinstance(overrides, dict) else None
    value = item.get("base_value") if override is None else override
    text = _text(value)
    return text or None


def _repair_plan(schedule: dict[str, Any]) -> bool:
    before = deepcopy(schedule)
    entries: list[tuple[dict[str, Any], bool]] = []
    for item in schedule.get("items") or []:
        if not isinstance(item, dict) or not isinstance(item.get("claim_limit"), dict):
            continue
        repaired, candidate = _repair_setting(
            item["claim_limit"], _plan_source(item), _plan_wording(item)
        )
        item["claim_limit"] = repaired
        entries.append((item, candidate))

    reserved = {
        scope
        for item, candidate in entries
        if not candidate
        for scope in item["claim_limit"].get("claim_scope_codes") or []
    }
    assigned = set(reserved)
    for item, candidate in entries:
        if not candidate:
            continue
        setting = item["claim_limit"]
        scopes = [
            scope
            for scope in setting.get("claim_scope_codes") or []
            if scope not in assigned
        ]
        if scopes != (setting.get("claim_scope_codes") or []):
            setting["claim_scope_codes"] = scopes
            if setting.get("status") in {"verified", "not_limit"}:
                setting["status"] = "needs_review"
        assigned.update(scopes)
    return schedule != before


def _repair_setup(sob: dict[str, Any]) -> bool:
    before = deepcopy(sob)
    column_ids = [
        str(column.get("id") or "")
        for column in sob.get("columns") or []
        if isinstance(column, dict) and column.get("id")
    ]
    entries: list[tuple[dict[str, Any], str, bool]] = []
    for item in sob.get("items") or []:
        if not isinstance(item, dict) or not isinstance(item.get("claim_limits"), dict):
            continue
        limits = dict(item["claim_limits"])
        for column_id in column_ids:
            setting = limits.get(column_id)
            if not isinstance(setting, dict):
                continue
            repaired, candidate = _repair_setting(
                setting,
                _setup_source(item, column_id),
                _setup_wording(item, column_id),
            )
            limits[column_id] = repaired
            entries.append((item, column_id, candidate))
        item["claim_limits"] = limits

    reserved = {column_id: set() for column_id in column_ids}
    for item, column_id, candidate in entries:
        if not candidate:
            reserved[column_id].update(
                item["claim_limits"][column_id].get("claim_scope_codes") or []
            )
    assigned = {column_id: set(scopes) for column_id, scopes in reserved.items()}
    for item, column_id, candidate in entries:
        if not candidate:
            continue
        setting = item["claim_limits"][column_id]
        scopes = [
            scope
            for scope in setting.get("claim_scope_codes") or []
            if scope not in assigned[column_id]
        ]
        if scopes != (setting.get("claim_scope_codes") or []):
            setting["claim_scope_codes"] = scopes
            if setting.get("status") in {"verified", "not_limit"}:
                setting["status"] = "needs_review"
        assigned[column_id].update(scopes)
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
                "SELECT plan.id, plan.benefit_schedule "
                f"FROM {prefix}plans AS plan"
            )
        ).fetchall()
        for plan_id, raw_schedule in plans:
            schedule = _as_dict(raw_schedule)
            if schedule is not None and _repair_plan(schedule):
                connection.execute(
                    plans_table.update()
                    .where(plans_table.c.id == plan_id)
                    .values(benefit_schedule=schedule)
                )
                plan_updates += 1

        setups = connection.execute(
            sa.text(f"SELECT id, answers FROM {prefix}product_setups")
        ).fetchall()
        for setup_id, raw_answers in setups:
            answers = _as_dict(raw_answers)
            sob = answers.get("sob") if answers else None
            if not isinstance(answers, dict) or not isinstance(sob, dict):
                continue
            if _repair_setup(sob):
                connection.execute(
                    setups_table.update()
                    .where(setups_table.c.id == setup_id)
                    .values(answers=answers)
                )
                setup_updates += 1
    print(f"[repair_copay_claim_limits] plans={plan_updates} setups={setup_updates}")


def downgrade() -> None:
    # Forward-only: restoring unsafe inferred currency values would be harmful.
    pass
