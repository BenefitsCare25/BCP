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
    "identification no.": "id_no",
    "date of birth": "date_of_birth",
    "gender": "gender",
    "marital status": "marital_status",
    "employment status": "employment_status",
    "designation": "designation",
    "job title": "designation",
    "position": "designation",
    "country of work": "country_of_work",
    "foreigner employment pass": "pass",
    "nationality": "nationality",
    "monthly salary": "salary",
    "currency": "currency",
    "date of hire": "date_of_hire",
    "confirmation date": "confirmation_date",
    "effective date": "effective_date",
    "last day of service": "last_day_of_service",
    "category": "category",
    "job grade": "job_grade",
    "division": "division",
    "department": "department",
    "cost centre": "cost_centre",
    "email": "email",
    "mobile": "mobile",
    "bank code": "bank_code",
    "branch code": "branch_code",
    "bank account no.": "bank_account_no",
    "bank account number": "bank_account_no",
    "has insurance cover last year": "prior_year_cover",
    "eligible to sell leave": "leave_sell_eligible",
    "remarks": "remarks",
}

# Attribute keys whose values are identifiers, not numbers — Excel often types
# them numeric ("081" arrives as int 81), so coerce to a clean string rather
# than persist a float. (Lost leading zeros can't be recovered here; the fix is
# a text-formatted column, which the download template provides.)
_CODE_KEYS = frozenset({
    "bank_code", "branch_code", "bank_account_no", "mobile", "id_no",
    "dependant_id_no", "employee_id_no",
})

# Attribute keys holding yes/no flags — normalized to real booleans on ingest.
_FLAG_KEYS = frozenset({"prior_year_cover", "leave_sell_eligible"})

# Insurer-issued member IDs ride dedicated per-insurer columns, e.g.
# "AIA Member ID" / "Zurich Member ID". Matched dynamically (any insurer name)
# and folded into an ``insurer_member_ids`` dict: {"AIA": "2427617201", ...}.
INSURER_MEMBER_ID_KEY = "insurer_member_ids"
_MEMBER_ID_RE = re.compile(r"^(?P<insurer>.+?)\s+member\s+id$", re.IGNORECASE)


DEPENDANT_COLUMN_MAP: dict[str, str] = {
    "entity": "entity",
    "staff id": "staff_id",
    "employee name": "employee_name",
    "employee's identification no. (nric/fin)": "employee_id_no",
    "employee's identification no.": "employee_id_no",
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


def _normalize_code(raw: Any) -> str:
    """Identifier-shaped value → clean string ('081' text stays '081'; a numeric
    cell 81 / 81.0 becomes '81' rather than '81.0')."""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


_TRUE_WORDS = {"true", "yes", "y", "1", "1.0"}
_FALSE_WORDS = {"false", "no", "n", "0", "0.0"}


def _normalize_flag(raw: Any) -> Any:
    """Yes/no-ish cell → real bool; unrecognized text is kept raw (visible in
    the roster rather than silently coerced)."""
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return raw


def _coerce_attr(attr_id: str, value: Any) -> Any:
    if attr_id in _CODE_KEYS:
        return _normalize_code(value)
    if attr_id in _FLAG_KEYS:
        return _normalize_flag(value)
    return value


def _member_id_columns(header_row: list[Cell]) -> dict[int, str]:
    """Column index → insurer name for '<Insurer> Member ID' headers, keeping
    the sheet's own casing for the insurer ('AIA Member ID' → 'AIA')."""
    out: dict[int, str] = {}
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        m = _MEMBER_ID_RE.match(re.sub(r"\s+", " ", str(cell)).strip())
        if m:
            out[idx] = m.group("insurer").strip()
    return out


def _collect_member_ids(
    row: list[Cell], member_id_cols: dict[int, str], attrs: dict[str, Any]
) -> None:
    ids = {
        insurer: _normalize_code(row[idx])
        for idx, insurer in member_id_cols.items()
        if idx < len(row) and row[idx] not in (None, "")
    }
    if ids:
        attrs[INSURER_MEMBER_ID_KEY] = ids


def parse_employee_workbook(path: Path | str) -> list[EmployeeRecord]:
    with open_workbook(path) as wb:
        sheet = wb.sheet(wb.sheet_names[0])
    if not sheet.rows:
        return []
    header_row = sheet.rows[0]
    col_map = _build_column_map(header_row, EMPLOYEE_COLUMN_MAP)
    member_id_cols = _member_id_columns(header_row)

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
                attrs[attr_id] = _coerce_attr(attr_id, value)
        _collect_member_ids(row, member_id_cols, attrs)
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
    member_id_cols = _member_id_columns(header_row)

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
                attrs[attr_id] = _coerce_attr(attr_id, value)
        _collect_member_ids(row, member_id_cols, attrs)
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
