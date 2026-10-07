"""Turn a parsed Employee Listing into roster records the movement engine diffs.

Pure: no database. Column roles become the same attribute ids the member
listing template uses, so the two upload paths agree about every field. The
listing's raw wording is kept (e.g. "NRIC Pink - Singaporean") alongside a
normalised work-pass code that eligibility rules can test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.el_workbook import ElColumn, ElEmployee, ElWorkbook
from app.services.el_workbook.values import text
from app.services.roster_parser import DependantRecord, EmployeeRecord, _coerce_attr

# Listing role -> roster attribute id. Roles absent here are not imported as
# attributes: identity fields ride the record itself, "age" is derived from
# the date of birth, and the movement column is free text for review.
EMPLOYEE_ATTRIBUTES: dict[str, str] = {
    "national_id": "id_no",
    "gender": "gender",
    "department": "department",
    "designation": "designation",
    "grade": "grade",
    "citizenship": "citizenship",
    "nationality": "nationality",
    "date_of_birth": "date_of_birth",
    "salary": "salary",
    "date_of_hire": "date_of_hire",
    "bank_code": "bank_code",
    "bank_branch": "branch_code",
    "bank_account": "bank_account_no",
    "email": "email",
    "mobile": "mobile",
    "entity": "entity",
    "country_of_work": "country_of_work",
    "marital_status": "marital_status",
    "cost_centre": "cost_centre",
    "currency": "currency",
    "last_day_of_service": "last_day_of_service",
    "remarks": "remarks",
    "retrenchment_start": "retrenchment_start",
    "retrenchment_end": "retrenchment_end",
    "reemployment_start": "reemployment_start",
    "reemployment_end": "reemployment_end",
}

DEPENDANT_ATTRIBUTES: dict[str, str] = {
    "name": "dependant_name",
    "relationship": "relationship",
    "gender": "gender",
    "national_id": "dependant_id_no",
    "nationality": "nationality",
    "residence": "country_of_residence",
    "date_of_birth": "date_of_birth",
}

PII_ATTRIBUTES = frozenset({
    "id_no", "date_of_birth", "salary", "bank_code", "branch_code", "bank_account_no",
    "email", "mobile", "nationality", "citizenship", "dependant_id_no",
})

# Work-pass wording -> the roster's ``pass`` codes (CITIZEN/PR/EP/SP/WP), plus
# LTVP/DP for listings that state those passes.
_PASS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("WP", r"work\s*permit|\bwp\b"),
    ("SP", r"\bs[\s-]*pass\b|\bspass\b"),
    ("EP", r"employment\s*pass|\bep\b"),
    ("LTVP", r"long\s*term\s*visit|\bltvp\b|\bploc\b"),
    ("DP", r"dependant'?s?\s*pass|\bdp\b"),
    ("PR", r"permanent\s*resident|nric\s*blue|\bpr\b"),
    ("CITIZEN", r"singaporean|citizen|nric\s*pink"),
)


def pass_code(value: Any) -> str | None:
    word = text(value).lower()
    if not word:
        return None
    return next((code for code, p in _PASS_PATTERNS if re.search(p, word)), None)


def slug(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.lower()).strip("_")[:64]


_MASKABLE = frozenset({"id_no", "dependant_id_no", "bank_account_no"})


def _masked(attr_id: str, value: Any) -> bool:
    """A masked identifier from a masked export ("S******7A") is not data: it
    must never overwrite the real number on file."""
    return attr_id in _MASKABLE and "*" in str(value)


def _value(attr_id: str, value: Any) -> Any:
    """Stored form: ISO dates, and the roster parser's normalisation for codes
    (IDs, bank details, mobile) so both upload paths store them identically.
    Other text keeps the listing's own wording ("Male", "FIN - Work Permit")."""
    if isinstance(value, date):
        return value.isoformat()
    return _coerce_attr(attr_id, value)


def employee_attribute_id(column: ElColumn, known_ids: frozenset[str]) -> str | None:
    """The attribute a listing column writes, or None when it writes none.

    A column whose own heading names an attribute the company already uses
    ("Depot" -> ``depot``) keeps that attribute; listing-specific category
    wording ("GHS Category") keeps its own heading so it can never be read as
    the matcher's ``category`` name tier.
    """
    if column.role in EMPLOYEE_ATTRIBUTES:
        return EMPLOYEE_ATTRIBUTES[column.role]
    if column.role in {"staff_id", "name", "age", "movement", "internal_note"}:
        return None
    own = slug(column.header)
    if column.role == "location":
        return own if own in known_ids else "location"
    return own or None


@dataclass(frozen=True)
class ListingRecords:
    employees: list[EmployeeRecord]
    dependants: list[DependantRecord]
    attribute_ids: dict[str, str]  # attribute id -> source heading


def build_records(workbook: ElWorkbook, known_ids: frozenset[str]) -> ListingRecords:
    layout = workbook.layout
    emp_block, dep_block = layout.employee, layout.dependant
    columns = [*(emp_block.columns if emp_block else ()), *layout.trailing]
    targets = {
        (c.role if c.role != "other" else f"other:{c.letter}"): (
            c, employee_attribute_id(c, known_ids)
        )
        for c in columns
    }
    attribute_ids: dict[str, str] = {}
    employees: list[EmployeeRecord] = []
    dependants: list[DependantRecord] = []
    for emp in workbook.employees:
        values = {**emp.fields, **emp.trailing}
        attrs: dict[str, Any] = {}
        for key, (column, attr_id) in targets.items():
            value = values.get(key)
            if attr_id is None or value in (None, "") or _masked(attr_id, value):
                continue
            attrs[attr_id] = _value(attr_id, value)
            attribute_ids.setdefault(attr_id, column.header)
        if (code := pass_code(emp.fields.get("citizenship"))) is not None:
            attrs["pass"] = code
            attribute_ids.setdefault("pass", "Work pass (from citizenship)")
        staff_id = text(emp.fields.get("staff_id"))
        name = text(emp.fields.get("name"))
        employees.append(EmployeeRecord(staff_id, name or None, attrs, row=emp.row))
        dependants.extend(_dependant_records(emp, staff_id, name, dep_block is not None))
    return ListingRecords(employees, dependants, attribute_ids)


def _dependant_records(
    emp: ElEmployee, staff_id: str, name: str, has_block: bool
) -> list[DependantRecord]:
    if not has_block:
        return []
    out: list[DependantRecord] = []
    for dep in emp.dependants:
        attrs: dict[str, Any] = {}
        for role, attr_id in DEPENDANT_ATTRIBUTES.items():
            value = dep.fields.get(role)
            if value in (None, "") or _masked(attr_id, value):
                continue
            if role == "relationship":
                value = str(value).capitalize()
            attrs[attr_id] = _value(attr_id, value)
        if staff_id:
            attrs["employee_staff_id"] = staff_id
        if name:
            attrs["employee_name"] = name
        out.append(DependantRecord(staff_id or None, name or None, None, attrs, row=dep.row))
    return out
