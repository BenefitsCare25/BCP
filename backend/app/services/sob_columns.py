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


def _effective(overrides: Any, col_id: str | None, base: Any) -> Any:
    """Resolve a cell: an ABSENT *or NULL* override inherits the base value.

    `None` must mean "inherit", not "blank" — the frontend mirror resolves with
    `??`, so treating a stored null as a real value made the broker see the base
    in the editor while the member got an empty row.
    """
    if col_id is None or not isinstance(overrides, dict):
        return base
    ov = overrides.get(col_id)
    return base if ov is None else ov


def _row_key(item: dict[str, Any]) -> str:
    """Identity of a benefit row across columns: its NAME.

    Deliberately NOT the number. ``placement_slip_sob`` auto-assigns
    ``number = str(len(items) + 1)`` for name-first products (GBT/OSI/GD), so a
    plan that omits an early row has every later row renumbered. Keying on the
    number would make those rows look distinct, and ``_union_rows`` would emit
    each benefit twice — exactly the missing-row case the union exists to
    handle.

    The name is already this system's benefit identity: ``claims.py`` and
    ``utilization.py`` both join on ``name.strip().lower()``. Names are unique
    within a plan; the number only orders and displays. A nameless row falls
    back to its number so blank rows don't all collide into one.
    """
    name = str(item.get("name") or "").strip().lower()
    return name or f"#{str(item.get('number') or '').strip().lower()}"


def _sub_key(sub: dict[str, Any]) -> str:
    name = str(sub.get("name") or "").strip().lower()
    return name or f"#{str(sub.get('key') or '').strip().lower()}"


def _union_rows(rep_items: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Every distinct row across all columns, in first-seen order.

    Column 0 defines the order it knows about; rows only a later column carries
    are appended after the row they followed there, so a richer plan's extra
    lines survive instead of being truncated away.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for items_ in rep_items:
        anchor = len(out)
        for row in items_:
            key = _row_key(row)
            if key in seen:
                # Re-anchor so this column's subsequent new rows land in the
                # right neighbourhood rather than all at the very end.
                anchor = next(
                    (i for i, r in enumerate(out) if _row_key(r) == key), anchor
                ) + 1
                continue
            seen.add(key)
            out.insert(anchor, row)
            anchor += 1
    return out


# Qualifier keys the slip parser mirrors into `limits` as well as `properties`
# (placement_slip_sob._SOB_PROPERTY_PATTERNS). `limits` is SHARED across
# columns, so these can never be represented per-column — including them in the
# grouping vector would split columns on a difference the model cannot store.
# Mirrored in BenefitScheduleView.MIRRORED_INTO_LIMITS.
_SHARED_PROPERTY_KEYS = frozenset(
    {"maximum_days", "qualification_period", "co_insurance", "surgical_schedule"}
)


def _varying_props(item: dict[str, Any] | None) -> list[tuple[str, Any]]:
    """The per-plan-varying subset of a row's `properties`, ordered.

    Copay fields and the dental Panel/Non-Panel axis vary by plan; the parser's
    shared qualifier keys do not (see `_SHARED_PROPERTY_KEYS`).
    """
    props = (item or {}).get("properties") or {}
    if not isinstance(props, dict):
        return []
    return sorted((k, v) for k, v in props.items() if k not in _SHARED_PROPERTY_KEYS)


def _plan_vector(items: list[dict[str, Any]]) -> str:
    """Group key: only the genuinely per-plan-varying fields (item value,
    sub-item values, varying properties). Structural fields are shared.

    `properties` participates for every kind, not just copay — the dental axis
    (Panel/Non-Panel) lives there too, so two plans differing only in their
    panel limits must stay separate columns instead of collapsing into one.
    """
    vec: list[Any] = []
    for b in items:
        subs = [s.get("value") or "" for s in (b.get("sub_items") or []) if isinstance(s, dict)]
        vec.append([b.get("value") or "", subs, _varying_props(b)])
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

    # Row skeleton = the UNION of rows across every column, not just column 0's.
    # A later column carrying rows the first one lacks (a richer plan) used to
    # have them silently discarded on import.
    canonical = _union_rows(rep_items)
    # Rows are matched across columns by IDENTITY, not position — with a union
    # skeleton a column that lacks an early row would otherwise have every
    # later row shifted up by one.
    by_key = [{_row_key(r): r for r in items_} for items_ in rep_items]

    items: list[dict[str, Any]] = []
    for base in canonical:
        row_key = _row_key(base)
        # `properties` is PER-COLUMN whenever any plan differs on it — copay
        # fields and the dental Panel/Non-Panel axis both live there, and both
        # can legitimately vary by plan. A row where every plan agrees keeps
        # them on the shared bag so the common case stays compact.
        # `or {}` on the INNER lookup too: a row dict that omits `properties`
        # and one carrying an empty dict both mean "no qualifiers", so they must
        # not compare unequal and force a needless per-column split.
        first_props = _varying_props(by_key[0].get(row_key))
        per_column_props = any(
            _varying_props(by_key[ci].get(row_key)) != first_props
            for ci in range(1, len(columns))
        )
        # A column that genuinely lacks this row is EXCLUDED from it, which is
        # exactly what NOT_COVERED means — don't let it inherit the base. That
        # includes column 0, whose cell IS the base value.
        cells = [
            (c.get("value") or "") if (c := by_key[ci].get(row_key)) is not None
            else NOT_COVERED
            for ci in range(len(columns))
        ]
        base_value = cells[0] if cells else ""
        overrides: dict[str, Any] = {}
        column_properties: dict[str, dict[str, str]] = {}
        for ci, col in enumerate(columns):
            cell = by_key[ci].get(row_key)
            if ci > 0 and cells[ci] != base_value:
                overrides[col["id"]] = cells[ci]
            if per_column_props:
                column_properties[col["id"]] = {
                    str(k): str(val)
                    for k, val in ((cell or {}).get("properties") or {}).items()
                }

        sub_items: list[dict[str, Any]] = []
        for sub in base.get("sub_items") or []:
            sub_key = _sub_key(sub)
            sub_base = sub.get("value")
            sub_overrides: dict[str, Any] = {}
            for ci, col in enumerate(columns):
                if ci == 0:
                    continue
                cell = by_key[ci].get(row_key) or {}
                sv = next(
                    (
                        s.get("value")
                        for s in (cell.get("sub_items") or [])
                        if _sub_key(s) == sub_key
                    ),
                    None,
                )
                # Only a genuine DIFFERENCE is an override; `None` means
                # "inherit", so never persist it as one.
                if sv is not None and sv != sub_base:
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
                "properties": {} if per_column_props else dict(base.get("properties") or {}),
                "column_properties": column_properties if per_column_props else None,
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
        value = _effective(it.get("overrides"), col_id, it.get("base_value"))
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
            subs_out.append(
                {
                    "key": s.get("key") or "",
                    "name": s.get("name") or "",
                    "value": _effective(s.get("overrides"), col_id, s.get("base_value")),
                    "note": s.get("note"),
                    "limits": s.get("limits") or [],
                    # `kind` must survive to the stored schedule: it is the only
                    # type signal the member-facing renderer has, and without it
                    # a visit COUNT ("6") renders as a currency ("$6").
                    "kind": s.get("kind"),
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
                "kind": it.get("kind"),
            }
        )
    return out
