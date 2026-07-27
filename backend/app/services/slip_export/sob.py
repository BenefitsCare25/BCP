"""Schedule of Benefits + Plan Details.

Plans fold to columns through the same model the SOB editor uses, so what the
insurer receives is what the broker sees: plans whose value vectors match
collapse into one column, and a column is headed by every plan code it covers.
"""
from __future__ import annotations

import re
from typing import Any

from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from app.models import Plan
from app.services.slip_export.styles import (
    HEADER,
    SECTION,
    WRAP,
    border_row,
    style_row,
)
from app.services.sob_columns import sob_from_plan_items


def shared_cover(plans: list[Plan]) -> str | None:
    """The product-level "Cover :" sentence, when the plans agree on one.

    Slip-parsed GHS-style products stamp the SAME cover sentence on every plan
    ("Cover: Reimbursement of eligible inpatient expenses…") — that belongs on
    the Cover line above the SOB, not repeated per plan. A single plan whose
    description is a basis expression ("24x basic monthly salary") stays in
    Plan Details."""
    descs = {
        (p.cover_description or "").strip()
        for p in plans
        if (p.cover_description or "").strip()
    }
    if len(descs) != 1:
        return None
    text = next(iter(descs))
    n = sum(1 for p in plans if (p.cover_description or "").strip())
    if n > 1 or text.lower().startswith("cover"):
        return text
    return None


def _sob_value(entry: dict[str, Any], col_id: str, first: bool) -> Any:
    if first:
        return entry.get("base_value")
    overrides = entry.get("overrides") or {}
    return overrides.get(col_id, entry.get("base_value"))


def _sob_col_label(col: dict[str, Any]) -> str:
    """Reference-style column header: the base plan plus every plan sharing its
    schedule ("Plan 1/U01/U04/U06"), not the fold's "+N" shorthand."""
    codes = [str(c) for c in (col.get("plan_codes") or []) if str(c)]
    if len(codes) > 1:
        return "Plan " + "/".join(codes)
    return str(col.get("label") or (f"Plan {codes[0]}" if codes else ""))


def write_sob(ws: Worksheet, plans: list[Plan], cover: str | None) -> None:
    with_items = [
        {
            "code": p.code,
            "label": p.display_name,
            "benefit_items": (p.benefit_schedule or {}).get("items"),
        }
        for p in plans
        if isinstance(p.benefit_schedule, dict)
        and isinstance(p.benefit_schedule.get("items"), list)
        and p.benefit_schedule["items"]
    ]
    if not with_items:
        return
    sob = sob_from_plan_items(with_items)
    columns = sob.get("columns") or []
    items = sob.get("items") or []
    if not columns or not items:
        return

    ws.append([])
    # The stored sentence usually carries its own "Cover:" prefix — strip it,
    # the label cell already says it.
    cover_text = re.sub(r"(?i)^cover\s*:\s*", "", cover).strip() if cover else ""
    ws.append(["Cover :", cover_text])
    style_row(ws, font=HEADER, wrap_cols=(2,))
    ws.cell(row=ws.max_row, column=2).font = Font(bold=False)
    ws.append(["SCHEDULE OF BENEFITS / INSURER / PLAN"])
    style_row(ws, font=SECTION)
    ws.append(["No.", "Benefit"] + [_sob_col_label(col) for col in columns])
    style_row(ws, font=HEADER)
    sob_last_col = 2 + len(columns)
    border_row(ws, 1, sob_last_col)
    value_cols = range(3, 3 + len(columns))

    def _value_row(number: str, name: str, entry: dict[str, Any]) -> None:
        values = [
            _sob_value(entry, col.get("id"), i == 0)
            for i, col in enumerate(columns)
        ]
        ws.append([number, name, *values])
        row = ws.max_row
        ws.cell(row=row, column=2).alignment = WRAP
        for col in value_cols:
            ws.cell(row=row, column=col).alignment = WRAP
        border_row(ws, 1, sob_last_col)

    def _limit_rows(limits: Any, indent: str) -> None:
        for lim in limits or []:
            if not isinstance(lim, dict):
                continue
            label = str(lim.get("label") or "").strip()
            value = str(lim.get("value") or "").strip()
            text = f"{label}: {value}" if value else label
            if text:
                ws.append(["", f"{indent}· {text}"])
                border_row(ws, 1, sob_last_col)

    for it in items:
        name = str(it.get("name") or "")
        if it.get("note"):
            name = f"{name} — {it['note']}"
        _value_row(str(it.get("number") or ""), name, it)
        # Copay axes vary per column (dash-group copay) — one row per property.
        col_props = it.get("column_properties")
        if isinstance(col_props, dict) and col_props:
            keys: list[str] = []
            for props in col_props.values():
                for k in props or {}:
                    if k not in keys:
                        keys.append(k)
            for key in keys:
                values = [
                    (col_props.get(col.get("id")) or {}).get(key, "")
                    for col in columns
                ]
                ws.append(["", f"    · {key}", *values])
                border_row(ws, 1, sob_last_col)
        # Non-copay `properties` are machine-derived duplicates of the limits
        # (maximum_days ↔ "Maximum no. of days") — never rendered.
        _limit_rows(it.get("limits"), "    ")
        for sub in it.get("sub_items") or []:
            sub_name = str(sub.get("name") or "")
            if sub.get("note"):
                sub_name = f"{sub_name} — {sub['note']}"
            _value_row("", f"    {sub_name}", sub)
            _limit_rows(sub.get("limits"), "        ")


def write_plan_details(
    ws: Worksheet, plans: list[Plan], cover: str | None
) -> None:
    """Cover-basis metadata (GCI-style plan tiers) — only when a plan carries
    something the Cover line doesn't already say. A cover sentence shared by
    every plan is hoisted onto the Cover line, so rows whose only content is
    that repeated sentence are dropped entirely."""
    def _own_cover(p: Plan) -> str:
        desc = (p.cover_description or "").strip()
        return "" if cover is not None and desc == cover else desc

    rows = [
        (p, own)
        for p in plans
        if (own := _own_cover(p)) or p.annual_policy_limit or p.report_label
    ]
    if not rows:
        return
    ws.append([])
    ws.append(["Plan Details"])
    style_row(ws, font=SECTION)
    ws.append(["Plan", "Cover Description", "Annual Policy Limit", "Report Label"])
    style_row(ws, font=HEADER)
    border_row(ws, 1, 4)
    for p, own in rows:
        ws.append([
            p.code, own,
            p.annual_policy_limit or "", p.report_label or "",
        ])
        style_row(ws, wrap_cols=(2,))
        border_row(ws, 1, 4)
