"""Placement-slip style export of a policy year's configured products (.xlsx).

Reverses the intake direction: where the parser turns an insurer's placement
slip into Category/Plan rows, this workbook renders those rows back into a
slip-shaped document — one sheet per product carrying the category/rate table
and the Schedule of Benefits — so brokers can hand the configured state back
to a client or insurer for review. Config-only: no member PII, and every
premium figure is emitted as stored (GST-exclusive — grossing is a display
concern, never re-applied here).
"""
from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Plan, PolicyYear, Product, ProductTerm
from app.services.sob_columns import sob_from_plan_items

_TITLE = Font(bold=True, size=13)
_SECTION = Font(bold=True, size=11)
_HEADER = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
_MONEY = "#,##0.00"

# Canonical tier vocabulary (mirror of product_registry TIER_SCHEMES labels):
# composite employee tiers first, dependant-only tiers after. A slip-specific
# label persisted in plan_assignments.tier_labels overrides the canonical one.
_TIER_LABELS = {
    "EO": "Employee Only",
    "ES": "Employee + Spouse",
    "EC": "Employee + Children",
    "EF": "Employee + Family",
    "SO": "Spouse Only",
    "CO": "Child(ren) Only",
    "SC": "Spouse & Child(ren)",
    "FO": "Family Only",
}
_TIER_ORDER = {code: i for i, code in enumerate(_TIER_LABELS)}

_CATEGORY_HEADERS = [
    "Insured", "Category", "Participation", "Plan", "No. of Employees",
    "Basis", "Sum Insured (S$)", "Premium Rate", "Annual Premium (S$)", "Notes",
]
_CATEGORY_WIDTHS = [24, 44, 18, 10, 14, 20, 16, 14, 18, 32]

_SHEET_BAD_CHARS = set("[]:*?/\\")


def _sheet_title(base: str, taken: set[str]) -> str:
    """Excel-legal (≤31 chars, no []:*?/\\) and unique within the workbook."""
    clean = "".join(c for c in base if c not in _SHEET_BAD_CHARS).strip() or "Product"
    title = clean[:31]
    n = 2
    while title.lower() in taken:
        suffix = f" ({n})"
        title = clean[: 31 - len(suffix)] + suffix
        n += 1
    taken.add(title.lower())
    return title


def _set_widths(ws: Worksheet, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _append_header(ws: Worksheet, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[ws.max_row]:
        cell.font = _HEADER


def _money_cell(ws: Worksheet, row: int, col: int) -> None:
    ws.cell(row=row, column=col).number_format = _MONEY


def _participation_text(c: Category) -> str:
    detail = c.participation_detail if isinstance(c.participation_detail, dict) else {}
    raw = str(detail.get("raw") or "").strip()
    if raw:
        # Slip cells carry embedded newlines; a single line reads better here.
        return " ".join(raw.split())
    return c.participation_model or ""


def _category_notes(pa: dict[str, Any]) -> str:
    notes: list[str] = []
    if pa.get("member_scope") == "dependant":
        notes.append("Dependant cover")
    if pa.get("location_scope"):
        notes.append(f"Scope: {pa['location_scope']}")
    if pa.get("dependant_rate") is not None:
        notes.append(f"Dependant rate: {pa['dependant_rate']}")
    if pa.get("estimated_annual_earnings") is not None:
        notes.append(f"Est. annual earnings: {pa['estimated_annual_earnings']}")
    if pa.get("rate_basis") == "age_banded":
        notes.append("Age-banded voluntary rates (table below)")
    if pa.get("premium_note"):
        notes.append(str(pa["premium_note"]))
    return "; ".join(notes)


def _tier_rows(pa: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """(label, rate, premium) per rate tier, canonical tiers first."""
    tiers = pa.get("rate_tiers")
    if not isinstance(tiers, dict):
        return []
    labels = pa.get("tier_labels") if isinstance(pa.get("tier_labels"), dict) else {}
    out = []
    for code in sorted(tiers, key=lambda c: (_TIER_ORDER.get(c, 99), c)):
        cell = tiers.get(code) or {}
        if not isinstance(cell, dict):
            continue
        label = str(labels.get(code) or _TIER_LABELS.get(code) or code)
        out.append((f"{code} — {label}", cell.get("rate"), cell.get("premium")))
    return out


def _write_category_table(ws: Worksheet, categories: list[Category]) -> None:
    _append_header(ws, _CATEGORY_HEADERS)
    for c in categories:
        pa = c.plan_assignments if isinstance(c.plan_assignments, dict) else {}
        ws.append([
            pa.get("insured") or "",
            c.display_name,
            _participation_text(c),
            str(pa.get("plan_code") or ""),
            pa.get("num_employees"),
            pa.get("basis") or "",
            pa.get("sum_insured"),
            pa.get("premium_rate"),
            pa.get("annual_premium"),
            _category_notes(pa),
        ])
        row = ws.max_row
        ws.cell(row=row, column=2).alignment = _WRAP
        ws.cell(row=row, column=3).alignment = _WRAP
        ws.cell(row=row, column=10).alignment = _WRAP
        _money_cell(ws, row, 7)
        _money_cell(ws, row, 9)
        # Per-member tier pricing (EO/ES/… or dependant-only SO/CO) breaks out
        # beneath its category, mirroring the slip's tier sub-rows.
        for label, rate, premium in _tier_rows(pa):
            ws.append(["", f"    {label}", "", "", "", "", "", rate, premium, ""])
            _money_cell(ws, ws.max_row, 9)


def _write_voluntary_rates(ws: Worksheet, categories: list[Category]) -> None:
    blocks = [
        (c.display_name, c.plan_assignments["voluntary_rates"])
        for c in categories
        if isinstance(c.plan_assignments, dict)
        and isinstance(c.plan_assignments.get("voluntary_rates"), list)
        and c.plan_assignments["voluntary_rates"]
    ]
    if not blocks:
        return
    ws.append([])
    ws.append(["Voluntary rates (age-banded, per S$1,000 sum insured)"])
    ws.cell(row=ws.max_row, column=1).font = _SECTION
    for name, bands in blocks:
        ws.append([name])
        ws.cell(row=ws.max_row, column=1).font = _HEADER
        _append_header(ws, ["Age band", "Rate"])
        for band in bands:
            if not isinstance(band, dict):
                continue
            ws.append([str(band.get("label") or ""), band.get("rate")])


def _write_plan_table(ws: Worksheet, plans: list[Plan]) -> None:
    """Basis-of-cover table — only when a plan carries descriptive cover data."""
    rows = [
        p for p in plans
        if p.cover_description or p.annual_policy_limit or p.report_label
    ]
    if not rows:
        return
    ws.append([])
    ws.append(["Basis of Cover"])
    ws.cell(row=ws.max_row, column=1).font = _SECTION
    _append_header(
        ws, ["Plan", "Cover Description", "Annual Policy Limit", "Report Label"]
    )
    for p in rows:
        ws.append([
            p.code, p.cover_description or "",
            p.annual_policy_limit or "", p.report_label or "",
        ])
        ws.cell(row=ws.max_row, column=2).alignment = _WRAP


def _sob_value(entry: dict[str, Any], col_id: str, first: bool) -> Any:
    if first:
        return entry.get("base_value")
    overrides = entry.get("overrides") or {}
    return overrides.get(col_id, entry.get("base_value"))


def _write_sob(ws: Worksheet, plans: list[Plan]) -> None:
    """Schedule of Benefits, plans folded to columns (matching value vectors
    collapse — the same model the SOB editor uses)."""
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
    ws.append(["Schedule of Benefits"])
    ws.cell(row=ws.max_row, column=1).font = _SECTION
    _append_header(
        ws, ["No.", "Benefit"] + [str(col.get("label") or "") for col in columns]
    )
    value_cols = range(3, 3 + len(columns))

    def _value_row(number: str, name: str, entry: dict[str, Any]) -> None:
        values = [
            _sob_value(entry, col.get("id"), i == 0)
            for i, col in enumerate(columns)
        ]
        ws.append([number, name, *values])
        row = ws.max_row
        ws.cell(row=row, column=2).alignment = _WRAP
        for col in value_cols:
            ws.cell(row=row, column=col).alignment = _WRAP

    def _limit_rows(limits: Any, indent: str) -> None:
        for lim in limits or []:
            if not isinstance(lim, dict):
                continue
            label = str(lim.get("label") or "").strip()
            value = str(lim.get("value") or "").strip()
            text = f"{label}: {value}" if value else label
            if text:
                ws.append(["", f"{indent}· {text}"])

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
        elif isinstance(it.get("properties"), dict):
            for k, v in it["properties"].items():
                ws.append(["", f"    · {k}: {v}"])
        _limit_rows(it.get("limits"), "    ")
        for sub in it.get("sub_items") or []:
            sub_name = str(sub.get("name") or "")
            if sub.get("note"):
                sub_name = f"{sub_name} — {sub['note']}"
            _value_row("", f"    {sub_name}", sub)
            _limit_rows(sub.get("limits"), "        ")


def _coverage_window(py: PolicyYear, term: ProductTerm | None) -> str:
    start = term.coverage_start if term and term.coverage_start else py.start_date
    end = term.coverage_end if term and term.coverage_end else py.end_date
    if not start or not end:
        return ""
    return f"{start:%d %b %Y} to {end:%d %b %Y}"


def build_placement_slip_workbook(db: Session, py: PolicyYear) -> Workbook:
    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == py.id)
            .order_by(Category.priority)
        ).scalars()
    )
    plans = list(
        db.execute(
            select(Plan).where(Plan.policy_year_id == py.id).order_by(Plan.code)
        ).scalars()
    )
    terms = {
        t.product_id: t
        for t in db.execute(
            select(ProductTerm).where(ProductTerm.policy_year_id == py.id)
        ).scalars()
    }

    product_ids = {c.product_id for c in categories if c.product_id}
    product_ids |= {p.product_id for p in plans}
    products = sorted(
        db.execute(select(Product).where(Product.id.in_(product_ids))).scalars()
        if product_ids
        else [],
        key=lambda p: p.code,
    )
    cats_by_product: dict[str | None, list[Category]] = {}
    for c in categories:
        cats_by_product.setdefault(c.product_id, []).append(c)
    plans_by_product: dict[str, list[Plan]] = {}
    for p in plans:
        plans_by_product.setdefault(p.product_id, []).append(p)

    wb = Workbook()
    overview = wb.active
    overview.title = "Overview"
    _set_widths(overview, [26, 40, 22, 28, 12, 8])
    overview.append(["Placement Slip — Configured Products"])
    overview.cell(row=1, column=1).font = _TITLE
    overview.append(["Client", py.client.name if py.client else ""])
    overview.append(["Policy Year", str(py.year)])
    overview.append(["Period of Insurance", _coverage_window(py, None)])
    overview.append(["Status", py.status.value])
    overview.append([
        "Note", "All premium amounts are GST-exclusive, as extracted/configured.",
    ])
    for r in range(2, 7):
        overview.cell(row=r, column=1).font = _HEADER
    overview.append([])
    _append_header(
        overview,
        ["Code", "Product", "Insurer", "Coverage Period", "Categories", "Plans"],
    )
    for product in products:
        overview.append([
            product.code,
            product.display_name,
            product.insurer or "",
            _coverage_window(py, terms.get(product.id)),
            len(cats_by_product.get(product.id, [])),
            len(plans_by_product.get(product.id, [])),
        ])

    taken: set[str] = {overview.title.lower()}
    for product in products:
        ws = wb.create_sheet(_sheet_title(product.code, taken))
        _set_widths(ws, _CATEGORY_WIDTHS)
        ws.append([f"{product.code} — {product.display_name}"])
        ws.cell(row=1, column=1).font = _TITLE
        ws.append(["Insurer", product.insurer or ""])
        ws.append(["Period of Insurance", _coverage_window(py, terms.get(product.id))])
        ws.cell(row=2, column=1).font = _HEADER
        ws.cell(row=3, column=1).font = _HEADER
        ws.append([])

        prod_cats = cats_by_product.get(product.id, [])
        prod_plans = plans_by_product.get(product.id, [])
        if prod_cats:
            _write_category_table(ws, prod_cats)
            _write_voluntary_rates(ws, prod_cats)
        _write_plan_table(ws, prod_plans)
        _write_sob(ws, prod_plans)

    unassigned = cats_by_product.get(None, [])
    if unassigned:
        ws = wb.create_sheet(_sheet_title("Unassigned", taken))
        _set_widths(ws, _CATEGORY_WIDTHS)
        ws.append(["Unassigned categories"])
        ws.cell(row=1, column=1).font = _TITLE
        ws.append([])
        _write_category_table(ws, unassigned)

    return wb
