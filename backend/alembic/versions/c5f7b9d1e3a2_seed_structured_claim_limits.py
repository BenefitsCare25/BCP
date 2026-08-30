"""seed structured claim-limit settings in existing schedules

Revision ID: c5f7b9d1e3a2
Revises: b4e6a8c0d2f1
Create Date: 2026-08-30

This data-only migration preserves the limit behaviour that existed before the
broker editor: obvious annual/per-unit SoB wording becomes a ``needs_review``
setting, and existing plan annual limits become reviewable overall settings.
No value is marked verified automatically. The migration is idempotent and
updates public plus every provisioned firm schema.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "c5f7b9d1e3a2"
down_revision: str | None = "b4e6a8c0d2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_PER_VISIT_RE = re.compile(r"(?:/|\bper\s+)(?:visit|consult(?:ation)?)\b", re.I)
_PER_DAY_RE = re.compile(r"(?:/|\bper\s+)(?:day|night)\b|\bdaily\b", re.I)
_PER_YEAR_RE = re.compile(r"\bper\s+(?:policy\s+)?year\b|\bper\s+annum\b|/year\b", re.I)


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


def _amount(value: object) -> float | None:
    match = _AMOUNT_RE.search(str(value or ""))
    if match is None:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
        return amount if amount >= 0 else None
    except ValueError:
        return None


def _basis(value: object, *, overall: bool = False) -> str | None:
    text = " ".join(str(value or "").split())
    folded = text.casefold()
    if not text or folded == "not covered":
        return None
    if "as charged" in folded:
        return "as_charged"
    if "%" in text:
        return "percentage"
    if _PER_DAY_RE.search(text):
        return "per_day"
    if _PER_VISIT_RE.search(text):
        return "per_visit"
    if "lifetime" in folded:
        return "lifetime"
    if _PER_YEAR_RE.search(text):
        return "policy_year"
    if _amount(text) is not None and (
        overall or any(token in folded for token in ("$", "sgd", "limit", "maximum", "max "))
    ):
        return "policy_year"
    return None


def _setting(
    value: object,
    scopes: list[str] | None = None,
    *,
    overall: bool = False,
) -> dict[str, Any] | None:
    basis = _basis(value, overall=overall)
    if basis is None:
        return None
    display = " ".join(str(value or "").split()) or None
    return {
        "basis": basis,
        "amount": _amount(display) if basis == "policy_year" else None,
        "currency": "SGD",
        "display": display,
        "claim_scope_codes": scopes or [],
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
    if code in {"SP", "GCSP", "GOSP"} and any(
        word in name for word in ("specialist", "consultation")
    ):
        return ["standard"]
    if code in {"GD", "DENTAL"} and "dental" in name:
        return ["standard"]
    if code in {"GHS", "GHS2", "IMP"}:
        if "pre" in name and "post" in name and "hospital" in name:
            return ["ghs_pre_post"]
        if any(word in name for word in ("dialysis", "cancer treatment")):
            return ["ghs_dialysis_cancer"]
        if any(word in name for word in ("emergency", "a&e", "accidental outpatient")):
            return ["ghs_emergency_outpatient"]
        if any(word in name for word in ("hospitalisation", "hospitalization", "day surgery")):
            return ["ghs_hospitalisation"]
    return []


def _uid(item: dict[str, Any]) -> str:
    key = " ".join(str(item.get("name") or item.get("number") or "benefit").split()).casefold()
    return "benefit-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _seed_plan_schedule(schedule: dict[str, Any], product_code: str, annual: object) -> bool:
    before = deepcopy(schedule)
    if "claim_limit" not in schedule:
        overall = _setting(annual, overall=True)
        if overall:
            schedule["claim_limit"] = overall
    assigned: set[str] = set()
    for item in schedule.get("items") or []:
        if not isinstance(item, dict):
            continue
        item.setdefault("uid", _uid(item))
        current = item.get("claim_limit")
        if isinstance(current, dict):
            assigned.update(current.get("claim_scope_codes") or [])
            continue
        scopes = [
            scope
            for scope in _scopes(product_code, item.get("name"))
            if scope not in assigned
        ]
        suggested = _setting(item.get("value"), scopes)
        if suggested:
            item["claim_limit"] = suggested
            assigned.update(scopes)
    return schedule != before


def _effective(item: dict[str, Any], column_id: str) -> object:
    overrides = item.get("overrides")
    if isinstance(overrides, dict) and overrides.get(column_id) is not None:
        return overrides[column_id]
    return item.get("base_value")


def _seed_setup_sob(
    sob: dict[str, Any], product_code: str, annual_by_plan: dict[str, object]
) -> bool:
    before = deepcopy(sob)
    columns = [column for column in (sob.get("columns") or []) if isinstance(column, dict)]
    assigned = {str(column.get("id") or ""): set() for column in columns}
    for item in sob.get("items") or []:
        if not isinstance(item, dict):
            continue
        item.setdefault("uid", _uid(item))
        existing = item.get("claim_limits")
        limits = dict(existing) if isinstance(existing, dict) else {}
        for column in columns:
            column_id = str(column.get("id") or "")
            if not column_id:
                continue
            current = limits.get(column_id)
            if isinstance(current, dict):
                assigned[column_id].update(current.get("claim_scope_codes") or [])
                continue
            scopes = [
                scope
                for scope in _scopes(product_code, item.get("name"))
                if scope not in assigned[column_id]
            ]
            suggested = _setting(_effective(item, column_id), scopes)
            if suggested:
                limits[column_id] = suggested
                assigned[column_id].update(scopes)
        if limits:
            item["claim_limits"] = limits
    plan_limits = dict(sob.get("plan_claim_limits") or {})
    for code, annual in annual_by_plan.items():
        if code not in plan_limits and (suggested := _setting(annual, overall=True)):
            plan_limits[code] = suggested
    if plan_limits:
        sob["plan_claim_limits"] = plan_limits
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
                "SELECT plan.id, plan.policy_year_id, plan.code, product.code, "
                "plan.annual_policy_limit, plan.benefit_schedule "
                f"FROM {prefix}plans AS plan JOIN {prefix}products AS product "
                "ON product.id = plan.product_id"
            )
        ).fetchall()
        annual_by_setup: dict[tuple[str, str], dict[str, object]] = {}
        for plan_id, year_id, plan_code, product_code, annual, raw_schedule in plans:
            code = str(plan_code or "").strip()
            product = str(product_code or "").strip().upper()
            annual_by_setup.setdefault((str(year_id), product), {})[code] = annual
            schedule = _as_dict(raw_schedule) or {"items": []}
            if _seed_plan_schedule(schedule, product, annual):
                connection.execute(
                    plans_table.update()
                    .where(plans_table.c.id == plan_id)
                    .values(benefit_schedule=schedule)
                )
                plan_updates += 1

        setups = connection.execute(
            sa.text(f"SELECT id, policy_year_id, product_code, answers FROM {prefix}product_setups")
        ).fetchall()
        for setup_id, year_id, product_code, raw_answers in setups:
            answers = _as_dict(raw_answers)
            sob = answers.get("sob") if answers else None
            if not isinstance(answers, dict) or not isinstance(sob, dict):
                continue
            product = str(product_code or "").strip().upper()
            annuals = annual_by_setup.get((str(year_id), product), {})
            if _seed_setup_sob(sob, product, annuals):
                connection.execute(
                    setups_table.update()
                    .where(setups_table.c.id == setup_id)
                    .values(answers=answers)
                )
                setup_updates += 1
    print(f"[seed_structured_claim_limits] plans={plan_updates} setups={setup_updates}")


def downgrade() -> None:
    # Intentionally irreversible: removing reviewable metadata would restore
    # runtime guessing and could re-enable a row a broker marked "not a limit".
    pass
