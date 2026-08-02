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

Mirror of ``frontend/src/lib/sob.ts`` — keep the two in sync, with ONE
documented exception: the blank-cell-inherits rule in ``sob_from_plan_items``
depends on the parser's ``not_applicable`` flag to tell a blank cell from an
explicit "NA". The frontend's ``buildSobFromPlans`` only ever folds LEGACY
per-plan drafts, where both were already flattened to null, so it deliberately
keeps the old "" behaviour — see the note at that call site.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

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


def benefit_row_key(name: Any, fallback: Any = None) -> str:
    """Identity of a benefit row: its NAME, cased and spaced consistently.

    THE one spelling of benefit-row identity — ``tier_differences`` compares two
    plans' schedules with it, and must agree with the fold here or the two
    disagree about whether a row changed. It grew a second, subtly different
    copy once already (that one collapsed internal whitespace and had no
    fallback), which made "Room  & Board" one row to the enrollment diff and two
    to the column fold.

    Deliberately NOT the number. ``placement_slip_sob`` auto-assigns
    ``number = str(len(items) + 1)`` for name-first products (GBT/OSI/GD), so a
    plan that omits an early row has every later row renumbered. Keying on the
    number would make those rows look distinct, and ``_union_rows`` would emit
    each benefit twice — exactly the missing-row case the union exists to
    handle.

    The name is already this system's benefit identity: ``claims.py`` and
    ``utilization.py`` both join on it. Names are unique within a plan; the
    number only orders and displays. Internal whitespace is collapsed because a
    slip cell wrapped across two lines arrives with a double space and is the
    same benefit. A nameless row falls back to ``fallback`` (its number, or a
    sub-item's key) so blank rows don't all collide into one.
    """
    key = " ".join(str(name or "").split()).lower()
    if key:
        return key
    return f"#{' '.join(str(fallback or '').split()).lower()}"


def _row_key(item: dict[str, Any]) -> str:
    return benefit_row_key(item.get("name"), item.get("number"))


def _sub_key(sub: dict[str, Any]) -> str:
    return benefit_row_key(sub.get("name"), sub.get("key"))


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
    """Name a column after the slip's own SOB header when the plans carry one.

    A composite header ("PLAN 1/U01/U04/U06") names several plan codes at once,
    and those codes are fanned out into one plan each — so a column usually
    regroups exactly the plans of one header and can print it verbatim. That is
    what the broker sees on the slip, so it beats the synthetic "Plan 1 +3"
    summary, which silently drops which codes the column actually covers.

    Value-identical headers can merge into one column (CDL's GMM prices PLAN 3
    and PLAN 4 the same), so join the DISTINCT headers rather than assuming one.
    Plans with no header — descriptive single-schedule sheets, manually-built
    drafts — fall back to the previous summary form.
    """
    # EVERY member must be named by a header, else the label would advertise a
    # code set narrower than the column really covers (VDL's GCSP groups a B3
    # that its "Plan B2, B1, B, A1, A" header never mentions).
    labels: list[str] = []
    for m in members:
        src = str(m.get("source_label") or "").strip()
        if not src:
            labels = []
            break
        if src not in labels:
            labels.append(src)
    if labels:
        return " + ".join(labels)
    if len(members) == 1:
        m = members[0]
        return str(m.get("label") or m.get("code") or "")
    if only_column:
        return "All plans"
    first = members[0]
    label = str(first.get("label") or first.get("code") or "")
    return f"{label} +{len(members) - 1}" if len(members) > 1 else label


def sob_from_plan_items(
    plans: list[dict[str, Any]], *, blank_inherits: bool = True
) -> dict[str, Any]:
    """Build a ``{columns, items}`` schedule by de-duplicating per-plan grids.

    Plans whose value vectors match collapse to one column. The first column's
    representative supplies each row's ``base_value``; the rest carry a sparse
    override only where they differ. ``id``s are deterministic (``col0``, ``col1``
    …) so the projection is reproducible without a RNG.

    ``blank_inherits`` distinguishes the two kinds of input this takes, and
    getting it wrong misstates cover in one direction or the other:

    * **Freshly PARSED slip grids** (the default) carry ``not_applicable``, so a
      blank cell is genuinely "the slip stated this once across the span" and
      must inherit — see the three-state note below.
    * **Already-RESOLVED plan schedules** (``slip_export``, re-folding
      ``Plan.benefit_schedule``) carry no ``na`` flag at all, and their blanks
      are the OPPOSITE thing: a broker who cleared a cell in the editor stores a
      real ``""``, and a row the slip said "NA" to was flattened to ``None``
      when it was written. Inheriting either one prints another plan's value in
      that column of the exported placement slip — a legal document. Pass
      ``False`` there, so a blank stands as an explicit blank.
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
        # Three states, and conflating any two of them misreports cover:
        #
        #   row ABSENT from this plan   → NOT_COVERED (an explicit exclusion —
        #                                 never let it inherit the base)
        #   cell said "NA"              → NOT_COVERED (the slip states the plan
        #                                 doesn't carry this benefit)
        #   cell BLANK                  → None, i.e. NOT STATED → inherit
        #
        # The last one is the fix. A blank cell under a later plan column is the
        # slip stating the value ONCE across the span, which is how these grids
        # are written; coercing it to "" made it an explicit empty override, and
        # `_effective` treats "" as a real value. CDL's GMM plans 2/3/4 rendered
        # Inpatient benefits, Daily Home Nursing and the GST extension as blank
        # rows on the member's own coverage page while the slip granted them
        # plan 1's values. `None` cannot be used for "NA" as well, which is why
        # the parser carries `not_applicable` separately — inheriting there
        # would OVERSTATE cover, the one error worse than the one being fixed.
        cells: list[Any] = []
        for ci in range(len(columns)):
            c = by_key[ci].get(row_key)
            if c is None or c.get("na"):
                cells.append(NOT_COVERED)
            elif blank_inherits:
                cells.append(c.get("value") or None)
            else:
                # Re-folding stored values: a blank is the broker's own blank
                # (or a flattened "NA"), never "not stated". Keep it verbatim.
                cells.append(c.get("value"))
        # Column 0's cell IS the base value and has nothing to inherit from, so
        # "not stated" is simply blank there.
        base_value = (cells[0] if cells else None) or ""
        overrides: dict[str, Any] = {}
        column_properties: dict[str, dict[str, str]] = {}
        for ci, col in enumerate(columns):
            cell = by_key[ci].get(row_key)
            # `None` = not stated → no override → inherits (a stored null would
            # mean the same thing, but writing nothing keeps the draft compact).
            if ci > 0 and cells[ci] is not None and cells[ci] != base_value:
                overrides[col["id"]] = cells[ci]
            if per_column_props:
                column_properties[col["id"]] = {
                    str(k): str(val)
                    for k, val in ((cell or {}).get("properties") or {}).items()
                }

        sub_items: list[dict[str, Any]] = []
        for sub in base.get("sub_items") or []:
            sub_key = _sub_key(sub)
            # An "NA" sub-cell is an explicit exclusion, not a missing value.
            # This branch has always inherited a `None`, which is right for a
            # blank — but "NA" also arrives as `None` (`_fmt_value` folds them),
            # so before `not_applicable` existed a plan whose sub-row read "NA"
            # silently inherited the richer plan's figure and OVERSTATED its
            # cover. GCSP's "Non Panel Specialists" is exactly that row.
            sub_base = NOT_COVERED if sub.get("na") else sub.get("value")
            sub_overrides: dict[str, Any] = {}
            for ci, col in enumerate(columns):
                if ci == 0:
                    continue
                cell = by_key[ci].get(row_key) or {}
                match = next(
                    (
                        s
                        for s in (cell.get("sub_items") or [])
                        if _sub_key(s) == sub_key
                    ),
                    None,
                )
                if match is None:
                    # The column doesn't carry this sub-row at all — inherit
                    # (sub-item lists legitimately differ in length across
                    # columns; absence here is not an assertion of exclusion).
                    continue
                sv = NOT_COVERED if match.get("na") else match.get("value")
                if sv is None and not blank_inherits:
                    # Re-folding stored values: this column HAS the sub-row and
                    # states nothing for it, which is a blank, not an inherit.
                    sv = ""
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
    """The column carrying this plan's values, or None when it maps to none.

    A SINGLE column covers the whole product by construction ("All plans"), so
    an unlisted code resolves to it unambiguously. With SEVERAL columns there is
    no safe guess: falling back to ``columns[0]`` handed the plan the FIRST
    column's schedule, which on a graded product is the richest one (CDL GHS
    col0 is 1-bed private / 22k in-patient). That silently OVER-STATES a
    member's cover and hides the mapping gap that caused it — VDL's GCGP has a
    real category code ("B3") with no schedule column. Report None instead and
    let the caller surface it.
    """
    columns = sob.get("columns") or []
    for col in columns:
        if plan_code in (col.get("plan_codes") or []):
            return col.get("id")
    return columns[0].get("id") if len(columns) == 1 else None


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
    if col_id is None and (sob.get("columns") or []):
        # Multi-column schedule with no column for this plan: we genuinely don't
        # know its values. Emitting an empty schedule is visibly wrong to the
        # broker; inheriting column 0 would be invisibly wrong to the member.
        logger.warning(
            "Plan %r maps to no Schedule-of-Benefits column (available: %r) — "
            "writing an empty schedule rather than inheriting another plan's.",
            plan_code,
            [c.get("plan_codes") for c in (sob.get("columns") or [])],
        )
        return []
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
