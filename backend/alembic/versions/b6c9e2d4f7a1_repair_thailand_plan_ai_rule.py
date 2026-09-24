"""Repair one unconfirmed Thailand plan rule copied from roster category text.

Revision ID: b6c9e2d4f7a1
Revises: f5a7c9e1b3d4
Create Date: 2026-09-24

The slip says J1-J3/JA-JC. Its Plan 1 AI proposal instead matched the roster's
broad text category and included an X1 employee. Copy the reviewed rule from
the same cohort's Plan 2 only when both saved rules and provenance still match
the incident exactly. The broker must still confirm the repaired Plan 1 row.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "b6c9e2d4f7a1"
down_revision: str | None = "f5a7c9e1b3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_DESCRIPTION = (
    "Officer and All Employees based in Thailand (except for Director) "
    "(Job Category: J1 to J3, JA to JC)"
)
_BAD_VALUES = {"All Employees based in Thailand (except for Director)", "Officer"}
_JOB_CODES = ["J1", "J2", "J3", "JA", "JB", "JC"]
_MISSING_ATTRIBUTE = "Matching rule omitted explicit employee attribute: job_category"
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _as_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.categories"},
        ):
            schemas.append(schema)
    return schemas


def _repair_schema(bind: sa.engine.Connection, schema: str | None) -> int:
    prefix = (
        bind.dialect.identifier_preparer.quote_schema(schema) + "."
        if schema is not None
        else ""
    )
    rows = bind.execute(
        sa.text(
            "SELECT id, policy_year_id, product_id, raw_description, source_ref, "
            "matching_rule, rule_validation, rule_status, status, source, "
            "human_modified FROM "
            f"{prefix}categories WHERE source_ref LIKE :source_hint"
        ),
        {"source_hint": "%/GCGP/row_24"},
    ).mappings().all()
    table = sa.table(
        "categories",
        sa.column("id", sa.String),
        sa.column("matching_rule", _JSON),
        sa.column("rule_human_readable", sa.String),
        sa.column("rule_validation", _JSON),
        sa.column("rule_status", sa.String),
        sa.column("mapping_profile_id", sa.String),
        sa.column("source", sa.String),
        sa.column("human_modified", sa.Boolean),
        sa.column("updated_at", sa.DateTime),
        schema=schema,
    )
    repaired = 0
    for bad in rows:
        bad_rule = _as_dict(bad["matching_rule"])
        validation = _as_dict(bad["rule_validation"]) or {}
        bad_args = bad_rule.get("in") if bad_rule else None
        if not (
            str(bad["source_ref"] or "").endswith("/GCGP/row_24")
            and bad["raw_description"] == _SOURCE_DESCRIPTION
            and bad["source"] == "ai_extracted"
            and bad["status"] == "needs_review"
            and not bad["human_modified"]
            and isinstance(bad_args, list)
            and len(bad_args) == 2
            and bad_args[0] == "category"
            and isinstance(bad_args[1], list)
            and all(isinstance(value, str) for value in bad_args[1])
            and set(bad_args[1]) == _BAD_VALUES
            and _MISSING_ATTRIBUTE in validation.get("errors", [])
        ):
            continue

        siblings = bind.execute(
            sa.text(
                "SELECT id, source_ref, matching_rule, rule_human_readable, "
                "rule_validation, rule_status, source, human_modified FROM "
                f"{prefix}categories WHERE policy_year_id = :policy_year_id "
                "AND product_id = :product_id AND raw_description = :description "
                "AND id <> :bad_id"
            ),
            {
                "policy_year_id": bad["policy_year_id"],
                "product_id": bad["product_id"],
                "description": _SOURCE_DESCRIPTION,
                "bad_id": bad["id"],
            },
        ).mappings().all()
        reviewed = [
            row
            for row in siblings
            if str(row["source_ref"] or "").endswith("/GCGP/row_31")
            and row["source"] == "manual"
            and row["human_modified"]
            and row["rule_status"] == "validated"
            and _as_dict(row["matching_rule"])
            == {"in": ["job_category", _JOB_CODES]}
        ]
        if len(reviewed) != 1:
            continue

        sibling = reviewed[0]
        sibling_validation = _as_dict(sibling["rule_validation"]) or {}
        repaired_validation = {
            **validation,
            "state": "validated",
            "source": "reviewed_sibling_repair",
            "errors": [],
            "warnings": sibling_validation.get("warnings", []),
            "overlap_count": 0,
            "unresolved_clauses": [],
            "required_attributes": ["job_category"],
            "matched_count": sibling_validation.get("matched_count"),
            "confirmed": False,
            "repaired_from_category_id": sibling["id"],
        }
        bind.execute(
            table.update()
            .where(table.c.id == bad["id"])
            .values(
                matching_rule={"in": ["job_category", _JOB_CODES]},
                rule_human_readable=(
                    sibling["rule_human_readable"]
                    or "job_category is one of " + ", ".join(_JOB_CODES)
                ),
                rule_validation=repaired_validation,
                rule_status="validated",
                mapping_profile_id=None,
                source="manual",
                human_modified=True,
                updated_at=sa.func.now(),
            )
        )
        repaired += 1
    return repaired


def upgrade() -> None:
    bind = op.get_bind()
    repaired = sum(_repair_schema(bind, schema) for schema in _schemas(bind))
    print(f"[repair_thailand_plan_ai_rule] repaired={repaired}")


def downgrade() -> None:
    # Forward-only: restoring a rule known to assign X1 employees to J plans
    # would reintroduce incorrect coverage.
    pass
