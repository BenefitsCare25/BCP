"""backfill `kind` onto stored benefit_schedule items

Revision ID: e4a6c8b0d2f1
Revises: d1f3b5a7c9e2
Create Date: 2026-07-20

`kind` (currency / percent / days / list / scale / …) is the only type signal
the read-only renderers have. It was previously dropped when a setup was
projected into `Plan.benefit_schedule`, so every stored row lost it and the
member-facing view had to guess from the digits — rendering a visit COUNT of
"6" as "$6", and showing a 30-entry covered-conditions list as 30 benefit rows.

The writers now persist it, but that is forward-only: existing rows have none.
This backfills them.

Two sources, in order of trust:

1. The product's own setup draft (`product_setups.answers.sob.items[].kind`) —
   EXACT, matched by row identity (number + name). This is the same data the
   broker authored, so it needs no guessing.
2. A deliberately conservative value-based inference for rows with no draft:
   only `percent` and `days`, which are unambiguous from their own text.
   Numeric rows are left NULL so the renderer's existing currency fallback
   keeps its current behaviour — guessing "currency" here is exactly the bug
   we are fixing, and a wrong guess is worse than none.

Data-only and idempotent: rows that already carry a kind are never touched.
"""
from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "e4a6c8b0d2f1"
down_revision = "d1f3b5a7c9e2"
branch_labels = None
depends_on = None


_PERCENT = re.compile(r"^\s*\d+(\.\d+)?\s*%\s*$")
_DAYS = re.compile(r"^\s*\d+\s*days?\s*$", re.IGNORECASE)


def _row_key(number: object, name: object) -> tuple[str, str]:
    """Mirrors sob_columns._row_key / lib/sob.ts rowKey."""
    return (str(number or "").strip().lower(), str(name or "").strip().lower())


def _sub_key(key: object, name: object) -> tuple[str, str]:
    return (str(key or "").strip().lower(), str(name or "").strip().lower())


def _infer(value: object) -> str | None:
    """Only what the text states outright; never guess currency."""
    if not isinstance(value, str):
        return None
    if _PERCENT.match(value):
        return "percent"
    if _DAYS.match(value):
        return "days"
    return None


def _as_dict(raw: object) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


# Lightweight table stub so the UPDATE goes through SQLAlchemy's JSON type
# rather than a raw text bind. Binding `json.dumps(...)` into a JSON/JSONB
# column via sa.text() relies on the driver coercing str -> json, which SQLite
# tolerates and Postgres does not; the suite is SQLite-only (see CLAUDE.md
# "Postgres exercised by the full pytest suite" under deferred), so the deploy
# migrate step would be the first place that surfaced. This mirrors the pattern
# in d5e6f7a89012.
_plans = sa.table(
    "plans",
    sa.column("id", sa.String),
    sa.column("benefit_schedule", sa.JSON),
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── Source 1: setup drafts, keyed (policy_year_id, product_code) ─────────
    # {(py_id, product_code): {row_key: kind}} and the sub-item equivalent.
    item_kinds: dict[tuple[str, str], dict[tuple[str, str], str]] = {}
    sub_kinds: dict[tuple[str, str], dict[tuple[str, str, str], str]] = {}

    setups = conn.execute(
        sa.text("SELECT policy_year_id, product_code, answers FROM product_setups")
    ).fetchall()
    for py_id, product_code, answers in setups:
        parsed = _as_dict(answers)
        if not parsed:
            continue
        sob = parsed.get("sob")
        if not isinstance(sob, dict) or not isinstance(sob.get("items"), list):
            continue
        bucket = item_kinds.setdefault((str(py_id), str(product_code or "").upper()), {})
        sbucket = sub_kinds.setdefault((str(py_id), str(product_code or "").upper()), {})
        for it in sob["items"]:
            if not isinstance(it, dict):
                continue
            rk = _row_key(it.get("number"), it.get("name"))
            kind = it.get("kind")
            if isinstance(kind, str) and kind:
                bucket[rk] = kind
            for s in it.get("sub_items") or []:
                if not isinstance(s, dict):
                    continue
                skind = s.get("kind")
                if isinstance(skind, str) and skind:
                    sk = _sub_key(s.get("key"), s.get("name"))
                    sbucket[(rk[0], rk[1], f"{sk[0]}|{sk[1]}")] = skind

    # ── Apply to every stored plan schedule ─────────────────────────────────
    plans = conn.execute(
        sa.text(
            "SELECT p.id, p.policy_year_id, p.benefit_schedule, pr.code "
            "FROM plans p LEFT JOIN products pr ON pr.id = p.product_id "
            "WHERE p.benefit_schedule IS NOT NULL"
        )
    ).fetchall()

    updated = 0
    for plan_id, py_id, raw, product_code in plans:
        schedule = _as_dict(raw)
        if not schedule or not isinstance(schedule.get("items"), list):
            continue
        key = (str(py_id), str(product_code or "").upper())
        by_row = item_kinds.get(key, {})
        by_sub = sub_kinds.get(key, {})

        changed = False
        for it in schedule["items"]:
            if not isinstance(it, dict):
                continue
            rk = _row_key(it.get("number"), it.get("name"))
            if not it.get("kind"):
                kind = by_row.get(rk) or _infer(it.get("value"))
                if kind:
                    it["kind"] = kind
                    changed = True
            for s in it.get("sub_items") or []:
                if not isinstance(s, dict) or s.get("kind"):
                    continue
                sk = _sub_key(s.get("key"), s.get("name"))
                kind = by_sub.get((rk[0], rk[1], f"{sk[0]}|{sk[1]}")) or _infer(
                    s.get("value")
                )
                if kind:
                    s["kind"] = kind
                    changed = True

        if changed:
            conn.execute(
                _plans.update()
                .where(_plans.c.id == plan_id)
                .values(benefit_schedule=schedule)
            )
            updated += 1

    print(f"[backfill_benefit_item_kind] updated {updated}/{len(plans)} plan schedules")


def downgrade() -> None:
    # Data-only and additive: `kind` is ignored by every reader that predates
    # it, so stripping it again would only re-introduce the mis-rendering.
    pass
