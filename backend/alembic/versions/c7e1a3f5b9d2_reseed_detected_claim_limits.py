"""re-seed detected claim-limit guesses with the corrected rules

Revision ID: c7e1a3f5b9d2
Revises: b6c9e2d4f7a1
Create Date: 2026-09-26

The import-time guesses stored in product-setup drafts were made by rules that
read "SGD 250 per tooth" / "S$15 million any one claim" as yearly S$ caps, lost
"As Charged up to Overall Annual Limit of $1,000", tagged "WhiteCoat Pediatric /
Specialist" with the whole SP claim type, and seeded line limits on products
members never claim in the portal. Each blocked or misled the broker.

Only UNTOUCHED guesses (source "detected", status "needs_review") are replaced;
every broker decision is kept. Also written: "Number of Visits" sub-lines as
"6 visits" (stored bare they render as S$6), and an EMPTY outpatient channel row
removed where a filled row of the same name exists (the duplicate an earlier
template overlay left). Public and provisioned firm schemas; idempotent.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any

import sqlalchemy as sa

from alembic import op

revision: str = "c7e1a3f5b9d2"
down_revision: str | None = "b6c9e2d4f7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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


def _is_untouched_guess(setting: object) -> bool:
    return (
        isinstance(setting, dict)
        and setting.get("source") == "detected"
        and setting.get("status") == "needs_review"
    )


# Em dash, en dash, bracket: "Non-Panel <em dash> per visit …" vs "Non Panel".
_NAME_BREAK_RE = re.compile("[" + chr(0x2014) + chr(0x2013) + "(]")


def _base_name(name: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _NAME_BREAK_RE.split(str(name or ""))[0].lower())


def _blank(item: dict[str, Any]) -> bool:
    values: list[object] = [item.get("base_value"), *(item.get("overrides") or {}).values()]
    values += list((item.get("properties") or {}).values())
    for props in (item.get("column_properties") or {}).values():
        values += list((props or {}).values())
    return (
        not item.get("claim_limits")
        and not item.get("sub_items")
        and all(not str(v or "").strip() for v in values)
    )


def _drop_empty_duplicate_channels(sob: dict[str, Any]) -> None:
    items = [i for i in sob.get("items") or [] if isinstance(i, dict)]
    filled = {
        _base_name(i.get("name")) for i in items if i.get("kind") == "copay" and not _blank(i)
    }
    sob["items"] = [
        i
        for i in sob.get("items") or []
        if not (
            isinstance(i, dict)
            and i.get("kind") == "copay"
            and _blank(i)
            and _base_name(i.get("name")) in filled
        )
    ]


def _repair_sob(sob: dict[str, Any], product_code: str | None) -> bool:
    from app.services.claim_limits import visit_count_value
    from app.services.sob_columns import seed_sob_claim_limits

    before = deepcopy(sob)
    for item in sob.get("items") or []:
        if not isinstance(item, dict):
            continue
        limits = item.get("claim_limits")
        if isinstance(limits, dict):
            kept = {cid: s for cid, s in limits.items() if not _is_untouched_guess(s)}
            if kept:
                item["claim_limits"] = kept
            else:
                item.pop("claim_limits", None)
        for sub in item.get("sub_items") or []:
            if not isinstance(sub, dict):
                continue

            def visits(value: object, label: object = sub.get("name")) -> object:
                if value is None:
                    return value
                counted = visit_count_value(label, value)
                return counted if counted.endswith(("visit", "visits")) else value

            if "base_value" in sub:
                sub["base_value"] = visits(sub["base_value"])
            if isinstance(sub.get("overrides"), dict):
                sub["overrides"] = {cid: visits(v) for cid, v in sub["overrides"].items()}
    _drop_empty_duplicate_channels(sob)
    seed_sob_claim_limits(sob, product_code=product_code)
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


def upgrade() -> None:
    connection = op.get_bind()
    updates = 0
    for schema in _schemas(connection):
        prefix = _prefix(connection, schema)
        table = sa.table(
            "product_setups",
            sa.column("id", sa.String),
            sa.column("answers", sa.JSON),
            schema=schema,
        )
        rows = connection.execute(
            sa.text(f"SELECT id, product_code, answers FROM {prefix}product_setups")
        ).fetchall()
        for setup_id, product_code, raw_answers in rows:
            answers = _as_dict(raw_answers)
            sob = answers.get("sob") if answers else None
            if not isinstance(answers, dict) or not isinstance(sob, dict):
                continue
            if _repair_sob(sob, product_code):
                connection.execute(
                    table.update().where(table.c.id == setup_id).values(answers=answers)
                )
                updates += 1
    print(f"[reseed_detected_claim_limits] setups={updates}")


def downgrade() -> None:
    # Forward-only: the replaced guesses were wrong; restoring them would
    # put the same false decisions back in front of brokers.
    pass
