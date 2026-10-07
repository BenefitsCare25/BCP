"""Cover consistency checks on an uploaded Employee Listing.

Findings are reported for the broker's review; none blocks the import. They
name rows, never personal data.
"""
from __future__ import annotations

from app.schemas.employee_listing import ListingCheck
from app.services.el_import.mapping import LabelSuggestion, normalize_label
from app.services.el_workbook import ElWorkbook
from app.services.el_workbook.values import text

# Family tier -> (spouse covered, child covered) it states.
_TIER_MEMBERS = {"EO": (False, False), "ES": (True, False), "EC": (False, True),
                 "EF": (True, True)}


def _check(code: str, message: str, rows: list[int]) -> ListingCheck | None:
    if not rows:
        return None
    return ListingCheck(code=code, message=message, count=len(rows), rows=rows[:50])


def cover_checks(workbook: ElWorkbook, decisions: list[LabelSuggestion]) -> list[ListingCheck]:
    layout = workbook.layout
    tiered = {
        i for i, b in enumerate(layout.blocks)
        if b.kind == "product" and b.column("family_group") is not None
    }
    with_admin = {
        i for i, b in enumerate(layout.blocks)
        if b.kind == "product" and b.column("admin_type") is not None
    }
    unmapped = {
        (d.block, d.key) for d in decisions if d.choice is None and not d.not_covered
    }
    no_cover: list[int] = []
    tier_mismatch: list[int] = []
    no_admin: list[int] = []
    orphan_cover: list[int] = []
    unmapped_rows: list[int] = []
    movement: list[int] = []
    for emp in workbook.employees:
        if not emp.covers:
            no_cover.append(emp.row)
        if text(emp.fields.get("movement")):
            movement.append(emp.row)
        own = {c.block: c for c in emp.covers}
        for cover in emp.covers:
            key = (cover.block, normalize_label(text(cover.values.get("category"))))
            if key in unmapped:
                unmapped_rows.append(emp.row)
            if cover.block in with_admin and not cover.values.get("admin_type"):
                no_admin.append(emp.row)
        for block in tiered & set(own):
            tier = own[block].values.get("family_group")
            covered = [
                d.fields.get("relationship") for d in emp.dependants
                if any(c.block == block for c in d.covers)
            ]
            stated = (bool(covered.count("spouse")), bool(covered.count("child")))
            if tier in _TIER_MEMBERS and _TIER_MEMBERS[tier] != stated:
                tier_mismatch.append(emp.row)
        for dep in emp.dependants:
            for cover in dep.covers:
                if cover.block not in own:
                    orphan_cover.append(dep.row)
                if cover.block in with_admin and not cover.values.get("admin_type"):
                    no_admin.append(dep.row)
    findings = [
        _check("unmapped_category", "Rows whose listed category is not yet mapped to a "
               "slip category.", sorted(set(unmapped_rows))),
        _check("no_cover", "Employees listed with no cover under any product.", no_cover),
        _check("family_tier_mismatch", "Family tier (EO/ES/EC/EF) does not match the "
               "dependants listed under the same product.", sorted(set(tier_mismatch))),
        _check("dependant_without_employee_cover", "Dependants covered under a product "
               "their employee is not covered under.", sorted(set(orphan_cover))),
        _check("missing_admin_type", "Covered rows without a type of administration "
               "(headcount / named).", sorted(set(no_admin))),
        _check("movement_note", "Rows carrying a movement note; review whether the "
               "change applies from a later date.", movement),
    ]
    return [f for f in findings if f is not None]
