"""Schedule-of-Benefits column model.

The SOB editor decouples *benefit columns* (the genuinely-varying benefit levels
— usually one for life/CI, ≤4 for GHS) from *basis-of-cover plans* (the
sum-insured tiers, which can be many: 22 for CDL's GCI). Rather than replicate
the whole grid into every plan, the draft stores it once as a shared row
skeleton (``items``) plus a small set of ``columns``, each mapping to ≥1 plan
code; the effective cell value for ``(item, column)`` is
``overrides[column_id]`` falling back to ``base_value``.

This module owns two operations:

* ``sob_from_plan_items`` — de-duplicate a legacy per-plan grid into the column
  model (used by the slip→setup projection).
* ``resolve_plan_schedule`` — project one plan's effective schedule back out
  (used at confirm time to write ``Plan.benefit_schedule``, whose shape is
  unchanged).

Mirror of ``frontend/src/lib/sob.ts`` — keep the two in sync.
"""

from __future__ import annotations

import json
from typing import Any

# Sentinel override value marking a per-column exclusion ("Not included in SOB"
# on a single plan). Stored as a normal value so every read path renders it
# without a special flag.
NOT_COVERED = "Not covered"


def _items_of(plan: dict[str, Any]) -> list[dict[str, Any]]:
    items = plan.get("benefit_items")
    return items if isinstance(items, list) else []


def _plan_vector(items: list[dict[str, Any]]) -> str:
    """Group key: only the genuinely per-plan-varying fields (item value,
    sub-item values, copay properties). Structural fields are shared."""
    vec: list[Any] = []
    for b in items:
        subs = [s.get("value") or "" for s in (b.get("sub_items") or []) if isinstance(s, dict)]
        copay = (
            sorted((b.get("properties") or {}).items())
            if b.get("kind") == "copay"
            else None
        )
        vec.append([b.get("value") or "", subs, copay])
    return json.dumps(vec, sort_keys=True, default=str)


def _column_label(members: list[dict[str, Any]], only_column: bool) -> str:
    if len(members) == 1:
        m = members[0]
        return str(m.get("label") or m.get("code") or "")
    if only_column:
        return "All plans"
    first = members[0]
    label = str(first.get("label") or first.get("code") or "")
    return f"{label} +{len(members) - 1}" if len(members) > 1 else label


def sob_from_plan_items(plans: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a ``{columns, items}`` schedule by de-duplicating per-plan grids.

    Plans whose value vectors match collapse to one column. The first column's
    representative supplies each row's ``base_value``; the rest carry a sparse
    override only where they differ. ``id``s are deterministic (``col0``, ``col1``
    …) so the projection is reproducible without a RNG.
    """
    with_items = [p for p in plans if isinstance(p.get("benefit_items"), list)]
    if not with_items:
        return {"columns": [], "items": []}

    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for p in with_items:
        key = _plan_vector(_items_of(p))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)

    only_column = len(order) == 1
    columns: list[dict[str, Any]] = []
    rep_items: list[list[dict[str, Any]]] = []
    for ci, key in enumerate(order):
        members = groups[key]
        columns.append(
            {
                "id": f"col{ci}",
                "label": _column_label(members, only_column),
                "plan_codes": [str(m.get("code") or "") for m in members],
            }
        )
        rep_items.append(_items_of(members[0]))

    canonical = rep_items[0] if rep_items else []
    items: list[dict[str, Any]] = []
    for idx, base in enumerate(canonical):
        base_value = base.get("value") or ""
        is_copay = base.get("kind") == "copay"
        overrides: dict[str, Any] = {}
        column_properties: dict[str, dict[str, str]] = {}
        for ci, col in enumerate(columns):
            cell = rep_items[ci][idx] if idx < len(rep_items[ci]) else {}
            v = cell.get("value") or ""
            if ci > 0 and v != base_value:
                overrides[col["id"]] = v
            if is_copay:
                column_properties[col["id"]] = {
                    str(k): str(val) for k, val in (cell.get("properties") or {}).items()
                }

        sub_items: list[dict[str, Any]] = []
        for si, sub in enumerate(base.get("sub_items") or []):
            sub_base = sub.get("value")
            sub_overrides: dict[str, Any] = {}
            for ci, col in enumerate(columns):
                if ci == 0:
                    continue
                rep_subs = rep_items[ci][idx].get("sub_items") if idx < len(rep_items[ci]) else None
                sv = (rep_subs[si].get("value") if rep_subs and si < len(rep_subs) else None)
                if sv != sub_base:
                    sub_overrides[col["id"]] = sv
            sub_items.append(
                {
                    "key": sub.get("key") or "",
                    "name": sub.get("name") or "",
                    "note": sub.get("note"),
                    "limits": sub.get("limits") or [],
                    "kind": sub.get("kind"),
                    "base_value": sub_base,
                    "overrides": sub_overrides,
                }
            )

        items.append(
            {
                "number": base.get("number") or "",
                "name": base.get("name") or "",
                "kind": base.get("kind") or "amount",
                "note": base.get("note"),
                "limits": base.get("limits") or [],
                "base_value": base_value,
                "overrides": overrides,
                "properties": {} if is_copay else dict(base.get("properties") or {}),
                "column_properties": column_properties if is_copay else None,
                "sub_items": sub_items,
            }
        )

    return {"columns": columns, "items": items}


def _column_id_for_plan(sob: dict[str, Any], plan_code: str) -> str | None:
    columns = sob.get("columns") or []
    for col in columns:
        if plan_code in (col.get("plan_codes") or []):
            return col.get("id")
    return columns[0].get("id") if columns else None


def resolve_plan_schedule(
    sob: dict[str, Any], plan_code: str, max_items: int
) -> list[dict[str, Any]]:
    """Project a single plan's effective benefit items out of the schedule.

    Returns a list of raw item dicts (``number/name/value/note/limits/sub_items/
    properties``); the caller applies its own scalar cleaning. ``value`` resolves
    via the plan's column (override → base); copay column-properties merge over
    shared axis properties.
    """
    items_in = sob.get("items")
    if not isinstance(items_in, list):
        return []
    col_id = _column_id_for_plan(sob, plan_code)
    out: list[dict[str, Any]] = []
    for it in items_in[:max_items]:
        if not isinstance(it, dict):
            continue
        overrides = it.get("overrides") or {}
        base = it.get("base_value")
        value = overrides.get(col_id, base) if col_id is not None else base
        properties = {
            str(k): str(v) for k, v in (it.get("properties") or {}).items()
        }
        col_props = (it.get("column_properties") or {}).get(col_id) if col_id else None
        if isinstance(col_props, dict):
            properties.update({str(k): str(v) for k, v in col_props.items()})
        subs_out: list[dict[str, Any]] = []
        for s in it.get("sub_items") or []:
            if not isinstance(s, dict):
                continue
            s_ov = s.get("overrides") or {}
            s_base = s.get("base_value")
            s_val = s_ov.get(col_id, s_base) if col_id is not None else s_base
            subs_out.append(
                {
                    "key": s.get("key") or "",
                    "name": s.get("name") or "",
                    "value": s_val,
                    "note": s.get("note"),
                    "limits": s.get("limits") or [],
                }
            )
        out.append(
            {
                "number": it.get("number") or "",
                "name": it.get("name") or "",
                "value": value,
                "note": it.get("note"),
                "limits": it.get("limits") or [],
                "sub_items": subs_out,
                "properties": properties,
            }
        )
    return out
