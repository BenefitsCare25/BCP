"""Read every employee and dependant row of an Employee Listing."""
from __future__ import annotations

from pathlib import Path

from app.services.el_workbook.layout import detect_layout
from app.services.el_workbook.models import (
    ElBlock,
    ElCover,
    ElDependant,
    ElEmployee,
    ElIssue,
    ElLayout,
    ElWorkbook,
)
from app.services.el_workbook.values import normalize, text
from app.services.excel_reader import Cell, open_workbook

_DATE_ROLES = {"date_of_birth", "date_of_hire", "last_day_of_service",
               "mu_letter_member", "mu_letter_insurer", "acceptance_date",
               "retrenchment_start", "retrenchment_end", "reemployment_start",
               "reemployment_end"}
# A row carrying one of these but no name is a person the reader cannot read,
# not a totals or notes line.
_EMPLOYEE_IDENTITY = {"staff_id", "national_id", "date_of_birth"}
_DEPENDANT_IDENTITY = {"national_id", "date_of_birth", "relationship"}


class ElFormatError(ValueError):
    """The workbook has no sheet laid out as an employee listing."""


def _cell(row: list[Cell], index: int) -> Cell:
    return row[index] if index < len(row) else None


def _block_values(
    block: ElBlock, row: list[Cell], row_no: int, issues: list[ElIssue]
) -> dict[str, object]:
    values: dict[str, object] = {}
    for column in block.columns:
        raw = _cell(row, column.index)
        key = column.role if column.role != "other" else f"other:{column.letter}"
        value = normalize(column.role, raw)
        if raw not in (None, "") and value is None and column.role in _DATE_ROLES:
            issues.append(ElIssue(
                row_no, column.header, "unreadable_date",
                f"Row {row_no} {column.letter}: the date could not be read; "
                "enter it as a date or day-first text.",
            ))
        values.setdefault(key, value)
    return values


def _covers(layout: ElLayout, row: list[Cell], row_no: int, issues: list[ElIssue]) -> list[ElCover]:
    out: list[ElCover] = []
    for index, block in enumerate(layout.blocks):
        if block.kind != "product":
            continue
        cover = ElCover(index, _block_values(block, row, row_no, issues))
        if not cover.is_empty:
            out.append(cover)
    return out


def _carries_identity(layout: ElLayout, row: list[Cell]) -> bool:
    identity = [
        column
        for block, roles in ((layout.employee, _EMPLOYEE_IDENTITY),
                             (layout.dependant, _DEPENDANT_IDENTITY))
        if block is not None
        for column in block.columns
        if column.role in roles
    ]
    return any(text(_cell(row, column.index)) for column in identity)


def read_rows(layout: ElLayout, rows: list[list[Cell]]) -> ElWorkbook:
    employee_block, dependant_block = layout.employee, layout.dependant
    issues: list[ElIssue] = []
    unread: list[int] = []
    emp_name = employee_block.column("name") if employee_block else None
    if employee_block is None or emp_name is None:
        raise ElFormatError("The listing has no employee name column.")
    emp_id = employee_block.column("staff_id")
    dep_name = dependant_block.column("name") if dependant_block else None

    employees: list[ElEmployee] = []
    by_staff_id: dict[str, ElEmployee] = {}
    for r in range(layout.header_row + 1, len(rows)):
        row = rows[r] or []
        row_no = r + 1
        if not any(v not in (None, "") for v in row):
            continue
        staff_id = text(_cell(row, emp_id.index)) if emp_id else ""
        dependant_name = text(_cell(row, dep_name.index)) if dep_name else ""
        if dependant_name and dependant_block is not None:
            owner = by_staff_id.get(staff_id) if staff_id else None
            if owner is None and not staff_id and employees:
                owner = employees[-1]
            if owner is None:
                issues.append(ElIssue(
                    row_no, "staff_id", "orphan_dependant",
                    f"Row {row_no}: dependant has no employee row with the same staff ID.",
                ))
                unread.append(row_no)
                continue
            if owner is not employees[-1]:
                issues.append(ElIssue(
                    row_no, "staff_id", "separated_dependant",
                    f"Row {row_no}: dependant is listed away from its employee row.",
                ))
            fields = _block_values(dependant_block, row, row_no, issues)
            if fields.get("relationship") is None:
                issues.append(ElIssue(
                    row_no, "relationship", "unknown_relationship",
                    f"Row {row_no}: relationship is not spouse or child.",
                ))
            owner.dependants.append(
                ElDependant(row_no, fields, _covers(layout, row, row_no, issues))
            )
            continue
        if not text(_cell(row, emp_name.index)):
            if _carries_identity(layout, row):
                unread.append(row_no)
            continue
        fields = _block_values(employee_block, row, row_no, issues)
        employee = ElEmployee(
            row_no,
            fields,
            _covers(layout, row, row_no, issues),
            {c.role: normalize(c.role, _cell(row, c.index)) for c in layout.trailing},
        )
        if staff_id:
            if staff_id in by_staff_id:
                issues.append(ElIssue(
                    row_no, "staff_id", "duplicate_staff_id",
                    f"Row {row_no}: staff ID repeats an earlier employee row.",
                ))
            by_staff_id.setdefault(staff_id, employee)
        else:
            issues.append(ElIssue(
                row_no, "staff_id", "missing_staff_id", f"Row {row_no}: staff ID is blank.",
            ))
        if fields.get("date_of_birth") is None:
            issues.append(ElIssue(
                row_no, "date_of_birth", "missing_dob",
                f"Row {row_no}: date of birth is blank; age limits cannot be applied.",
            ))
        employees.append(employee)
    return ElWorkbook(layout, employees, issues, unread)


def read_employee_listing(path: Path | str) -> ElWorkbook:
    """Parse the first sheet laid out as an employee listing."""
    with open_workbook(path) as wb:
        for name in wb.sheet_names:
            sheet = wb.sheet(name)
            layout = detect_layout(name, sheet.rows)
            if layout is not None and layout.employee is not None:
                return read_rows(layout, sheet.rows)
    raise ElFormatError("No sheet in the workbook is laid out as an employee listing.")
