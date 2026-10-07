"""resolve dependant cover the slip states but the import stored as "not covered"

Revision ID: 5d8e2a7c4b16
Revises: 16b00754757b
Create Date: 2026-10-08

A Participation cell usually states only the employee's mode ("Compulsory").
The slip import stored that as ``participation_detail.dependant = null``, and a
present null means the broker chose "Not covered" — so the slip's own evidence
was never read: "Directors and Eligible Dependents" priced EO/ES/EC/EF, or "…
and their Eligible Dependent", covered no dependants at all.

Only UNTOUCHED slip rows are recomputed (source "system_generated", never
edited: any broker PATCH sets ``human_modified``), from the same evidence the
import now reads (``dependant_coverage.slip_participation_detail``): the
row's wording, then its family-tier rates or dependant rate. The rules are
copied here so later code changes cannot alter what this migration did. Rows
the evidence leaves uncovered keep their null.

Where such a row is priced at one per-head rate with no dependant rate or
tier split, that rate also prices each dependant (``dependant_coverage.
per_head_dependant_rate``): "Directors and Eligible Dependents · headcount 8 ·
rate 710 · premium 5,680" charges the spouse the same 710. Public and
provisioned firm schemas; idempotent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "5d8e2a7c4b16"
down_revision: str | None = "16b00754757b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPLOYEE_ONLY_TIERS = {"EO", "E", "EE", "EMPLOYEE", "EMPLOYEE ONLY"}
_NEG_DEPENDANT = re.compile(
    r"(?:\bno\b|\bnot\b|\bnon[-\s]?|\bwithout\b|\bexcl)[\w\s.,/-]{0,15}depend", re.I
)
_VOLUNTARY_DEPENDANT = re.compile(
    r"depend\w*[\w\s]{0,20}\bvoluntary\b|\bvoluntary\b[\s\-:]{0,5}depend", re.I
)


def _as_dict(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _has_dependant_tier(pa: dict[str, Any]) -> bool:
    for field in ("rate_tiers", "tier_counts"):
        tiers = pa.get(field)
        if isinstance(tiers, dict) and any(
            str(k).strip().upper() not in _EMPLOYEE_ONLY_TIERS for k in tiers
        ):
            return True
    return False


def _mode(pa: dict[str, Any], display_name: str | None, raw: str | None) -> str | None:
    text = f"{display_name or ''} {raw or ''}".lower()
    voluntary = bool(_VOLUNTARY_DEPENDANT.search(text))
    if pa.get("dependant_rate") is not None or _has_dependant_tier(pa):
        return "voluntary" if voluntary else "compulsory"
    if "depend" in text:
        if _NEG_DEPENDANT.search(text):
            return None
        return "voluntary" if voluntary else "compulsory"
    return None


def _per_head_rate(pa: dict[str, Any]) -> bool:
    """One per-head rate, no dependant rate or tier split: it prices every
    insured person ("Directors and Eligible Dependents · 8 · 710 · 5,680")."""
    rate = pa.get("premium_rate")
    return (
        pa.get("rate_basis") in ("flat", "per_member")
        and isinstance(rate, (int, float))
        and not isinstance(rate, bool)
        and rate > 0
        and pa.get("dependant_rate") is None
        and not pa.get("rate_tiers")
    )


def _schemas(connection: sa.engine.Connection) -> list[str | None]:
    if connection.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for firm_id in connection.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        if connection.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.categories"},
        ):
            schemas.append(schema)
    return schemas


def _prefix(connection: sa.engine.Connection, schema: str | None) -> str:
    if schema is None:
        return ""
    return connection.dialect.identifier_preparer.quote_schema(schema) + "."


def upgrade() -> None:
    connection = op.get_bind()
    updated = 0
    for schema in _schemas(connection):
        prefix = _prefix(connection, schema)
        table = sa.table(
            "categories",
            sa.column("id", sa.String),
            sa.column("participation_detail", sa.JSON),
            sa.column("plan_assignments", sa.JSON),
            schema=schema,
        )
        rows = connection.execute(
            sa.text(
                "SELECT c.id, c.display_name, c.raw_description, c.plan_assignments, "
                "c.participation_detail, p.has_dependants "
                f"FROM {prefix}categories c JOIN {prefix}products p ON p.id = c.product_id "
                "WHERE c.source = 'system_generated' AND c.human_modified = :untouched"
            ),
            {"untouched": False},
        ).fetchall()
        for cat_id, name, raw, raw_pa, raw_detail, has_dependants in rows:
            detail = _as_dict(raw_detail)
            pa = _as_dict(raw_pa) or {}
            if detail is None or not has_dependants:
                continue
            mode = str(detail.get("dependant") or "").strip().lower()
            changed = False
            if "dependant" in detail and mode not in {"compulsory", "voluntary"}:
                resolved = _mode(pa, name, raw)
                if resolved is not None:
                    detail["dependant"] = mode = resolved
                    changed = True
            if mode in {"compulsory", "voluntary"} and _per_head_rate(pa):
                pa["dependant_rate"] = pa["premium_rate"]
                changed = True
            if not changed:
                continue
            connection.execute(
                table.update()
                .where(table.c.id == cat_id)
                .values(participation_detail=detail, plan_assignments=pa)
            )
            updated += 1
    print(f"[resolve_slip_dependant_cover] categories={updated}")


def downgrade() -> None:
    # Forward-only: the nulls being replaced misread the slip; restoring them
    # would take covered dependants off again.
    pass
