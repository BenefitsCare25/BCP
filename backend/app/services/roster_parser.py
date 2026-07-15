"""Parse STM-shaped employee + dependant upload templates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.excel_reader import Cell, open_workbook

# Column-name → attribute_id mapping for STM employee template.
EMPLOYEE_COLUMN_MAP: dict[str, str] = {
    # Legal entity employing the member (CDL/STM rosters title it "Entity").
    # Drives the insured-entity gate in matching for multi-subsidiary schemes.
    # No bare "company" alias — it would prefix-claim a "Company Email" column.
    "entity": "entity",
    "legal entity": "entity",
    "employer": "entity",
    "subsidiary": "entity",
    "company name": "entity",
    "staff id": "staff_id",
    "employee name": "employee_name",
    "identification no. (nric/fin)": "id_no",
    "date of birth": "date_of_birth",
    "gender": "gender",
    "marital status": "marital_status",
    "foreigner employment pass": "pass",
    "nationality": "nationality",
    "monthly salary": "salary",
    "date of hire": "date_of_hire",
    "confirmation date": "confirmation_date",
    "effective date": "effective_date",
    "category": "category",
    "job grade": "job_grade",
    "division": "division",
    "department": "department",
    "cost centre": "cost_centre",
    "email": "email",
    "mobile": "mobile",
}


DEPENDANT_COLUMN_MAP: dict[str, str] = {
    "entity": "entity",
    "staff id": "staff_id",
    "employee name": "employee_name",
    "employee's identification no. (nric/fin)": "employee_id_no",
    "dependant name": "dependant_name",
    "dependant's identification no.": "dependant_id_no",
    "relationship": "relationship",
    "date of marriage": "date_of_marriage",
    "gender": "gender",
    "date of birth": "date_of_birth",
    "effective date": "effective_date",
    "termination date": "termination_date",
    "remarks": "remarks",
}


@dataclass(frozen=True)
class EmployeeRecord:
    staff_id: str
    employee_name: str | None
    attributes: dict[str, Any]
    # 1-based row in the source workbook (header = row 1) — for user-facing
    # messages (e.g. the duplicate manifest), since blank/no-staff rows are
    # dropped and would otherwise desync a positional index from the sheet.
    row: int = 0


@dataclass(frozen=True)
class DependantRecord:
    employee_staff_id: str | None
    employee_name: str | None
    employee_id_no: str | None
    attributes: dict[str, Any]
    row: int = 0


def _normalize_pass(raw: Any) -> str | None:
    if not raw:
        return None
    s = str(raw).upper().replace("-", "").replace(" ", "")
    if s in {"SPASS", "S", "SP"} or "SPASS" in s:
        return "SP"
    if s in {"WP", "WORKPERMIT", "WORKPASS"}:
        return "WP"
    if s in {"EP", "EMPLOYMENTPASS"}:
        return "EP"
    return str(raw)


def _normalize_name(s: Any) -> str | None:
    if not s:
        return None
    return re.sub(r"[,\s]+", " ", str(s)).strip()


def parse_employee_workbook(path: Path | str) -> list[EmployeeRecord]:
    with open_workbook(path) as wb:
        sheet = wb.sheet(wb.sheet_names[0])
    if not sheet.rows:
        return []
    header_row = sheet.rows[0]
    col_map = _build_column_map(header_row, EMPLOYEE_COLUMN_MAP)

    records: list[EmployeeRecord] = []
    for offset, row in enumerate(sheet.rows[1:]):
        if not any(c is not None and str(c).strip() for c in row):
            continue
        attrs: dict[str, Any] = {}
        for idx, attr_id in col_map.items():
            if idx < len(row):
                value = row[idx]
                if value is None:
                    continue
                if attr_id == "pass":
                    value = _normalize_pass(value)
                if value in (None, ""):
                    continue
                attrs[attr_id] = value
        staff_id = attrs.pop("staff_id", None)
        if not staff_id:
            continue
        records.append(
            EmployeeRecord(
                staff_id=str(staff_id).strip(),
                employee_name=_normalize_name(attrs.get("employee_name")),
                attributes=attrs,
                row=offset + 2,  # header is row 1
            )
        )
    return records


def parse_dependant_workbook(path: Path | str) -> list[DependantRecord]:
    with open_workbook(path) as wb:
        sheet = wb.sheet(wb.sheet_names[0])
    if not sheet.rows:
        return []
    header_row = sheet.rows[0]
    col_map = _build_column_map(header_row, DEPENDANT_COLUMN_MAP)

    records: list[DependantRecord] = []
    for offset, row in enumerate(sheet.rows[1:]):
        if not any(c is not None and str(c).strip() for c in row):
            continue
        attrs: dict[str, Any] = {}
        for idx, attr_id in col_map.items():
            if idx < len(row):
                value = row[idx]
                if value in (None, ""):
                    continue
                attrs[attr_id] = value
        staff_id = attrs.pop("staff_id", None)
        employee_name = _normalize_name(attrs.pop("employee_name", None))
        employee_id_no = attrs.pop("employee_id_no", None)
        if not (staff_id or employee_name or employee_id_no):
            continue
        # Persist employee-identifying info in attribute_values so the auto-match
        # endpoint can re-run linking without re-uploading the roster.
        if staff_id:
            attrs["employee_staff_id"] = str(staff_id).strip()
        if employee_name:
            attrs["employee_name"] = employee_name
        if employee_id_no:
            attrs["employee_id_no"] = str(employee_id_no).strip()
        records.append(
            DependantRecord(
                employee_staff_id=str(staff_id).strip() if staff_id else None,
                employee_name=employee_name,
                employee_id_no=str(employee_id_no).strip() if employee_id_no else None,
                attributes=attrs,
                row=offset + 2,  # header is row 1
            )
        )
    return records


def _build_column_map(header_row: list[Cell], spec: dict[str, str]) -> dict[int, str]:
    """Map column index → attribute id. Each attribute binds to at most ONE
    column: exact header matches are assigned first (most reliable), then loose
    prefix matches fill remaining columns against still-unclaimed attributes.

    Binding each attribute once prevents a secondary header (e.g. "Job Grade
    Band" alongside "Job Grade") from prefix-matching the same attribute and
    overwriting the real value downstream (last-column-wins in the attrs dict).
    """
    keys: list[tuple[int, str]] = []
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        keys.append((idx, re.sub(r"\s+", " ", str(cell)).strip().lower()))

    out: dict[int, str] = {}
    taken: set[str] = set()
    # Pass 1 — exact matches win.
    for idx, key in keys:
        attr_id = spec.get(key)
        if attr_id is not None and attr_id not in taken:
            out[idx] = attr_id
            taken.add(attr_id)
    # Pass 2 — loose prefix match for slight variations ("Monthly Salary (SGD)"),
    # but only onto attributes no exact column already claimed.
    for idx, key in keys:
        if idx in out:
            continue
        for spec_key, attr_id in spec.items():
            if attr_id not in taken and key.startswith(spec_key):
                out[idx] = attr_id
                taken.add(attr_id)
                break
    return out
