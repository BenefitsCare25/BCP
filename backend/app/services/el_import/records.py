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
from app.services.el_workbook.models import ElDependant
from app.services.el_workbook.values import text, wef_date
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
# LTVP/DP/STP/ONE for listings that state those passes.
# First match wins. The S Pass's "S" must be a word of its own: in "Dependant's
# Pass" or "Student's Pass" it is a possessive, never the pass.
_PASS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("WP", r"work\s*permit|\bwp\b"),
    ("DP", r"depend[ae]nt['\u2019]?s?\s*pass|\bdp\b"),
    ("STP", r"student['\u2019]?s?\s*pass|\bstp\b"),
    ("LTVP", r"long\s*term\s*visit|\bltvp\b|\bploc\b"),
    ("ONE", r"overseas\s*networks?|\bone\s*pass\b"),
    ("SP", r"(?<![\w'\u2019])s[\s-]*pass\b|\bspass\b|\bsp\b"),
    ("EP", r"employment\s*pass|\bep\b"),
    ("PR", r"permanent\s*resident|nric\s*blue|\bs?pr\b"),
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


# A remark's WEF date is a cover START only when the remark adds the person
# ("Add newborn WEF 29 Aug 2026", "enrolled … WEF 14 Oct 2025"); "Delete
# spouse WEF 31 Mar 2026" states when cover ends and must never start it.
_ADDING = re.compile(
    r"\b(add(ed|ition)?|enrol+(ed|ment|s)?|join(ed|s|ing)?|new\s*-?\s*born|includ(e|ed|es))\b",
    re.IGNORECASE,
)
_ENDING = re.compile(
    r"\b(delet\w*|remov\w*|terminat\w*|ceas\w*|withdr\w*|exclud\w*|divorc\w*|deceas\w*"
    r"|resign\w*|left|end(s|ed|ing)?)\b",
    re.IGNORECASE,
)


# Where one statement in a remark ends and the next begins. A full stop is not
# a break: "w.e.f." and "1.10.2026" carry them.
_CLAUSE_BREAK = re.compile(r"[;,()&\n]|\b(?:and|but)\b", re.IGNORECASE)


def _clause_verb(clause: str) -> str | None:
    """"add", "end", "both" or None for one clause of a remark."""
    adding, ending = bool(_ADDING.search(clause)), bool(_ENDING.search(clause))
    if adding and ending:
        return "both"
    return "add" if adding else "end" if ending else None


def _stated_start(remark: Any) -> date | None:
    """The WEF date of the statement that adds the person: "Spouse added WEF
    1 Oct 2026, previous policy ended" starts cover on 1 Oct 2026.

    Each date belongs to its own clause's verb, or, in a clause without one
    ("Spouse added, WEF 1 Oct 2026"), to the nearest verb before it, else after
    it. A date an ending word governs, alone or alongside an adding one, never
    starts cover.
    """
    clauses = [c for c in _CLAUSE_BREAK.split(text(remark)) if c.strip()]
    verbs = [_clause_verb(c) for c in clauses]
    for i, clause in enumerate(clauses):
        start = wef_date(clause)
        if start is None:
            continue
        verb = (
            verbs[i]
            or next((v for v in reversed(verbs[:i]) if v), None)
            or next((v for v in verbs[i + 1:] if v), None)
        )
        if verb == "add":
            return start
    return None


def dependant_cover_start(dep: ElDependant, period_start: date | None) -> date | None:
    """When a dependant added during the year starts cover, or None from the
    start of the period.

    An adding remark's "WEF <date>" states it; a newborn otherwise starts at
    birth. Cover never starts before the dependant is born, and a date on or
    before the period start is just the period start.
    """
    if period_start is None:
        return None
    born = dep.fields.get("date_of_birth")
    born = born if isinstance(born, date) else None
    stated = _stated_start(dep.trailing.get("remarks"))
    start = max((d for d in (stated, born) if d is not None), default=None)
    return start if start is not None and start > period_start else None


def build_records(
    workbook: ElWorkbook, known_ids: frozenset[str], period_start: date | None = None
) -> ListingRecords:
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
        dependants.extend(_dependant_records(
            emp, staff_id, name, dep_block is not None, period_start
        ))
    return ListingRecords(employees, dependants, attribute_ids)


def unreadable_rows(workbook: ElWorkbook) -> int:
    """Listing rows the reader could not turn into a member record: a dependant
    with no employee row, or an ID with no name. Employee rows with neither a
    Staff ID nor a readable NRIC are counted by the shared planner
    (`adc._plan_employees`), so both upload paths count them once.
    """
    return len(workbook.unread_rows)


def _dependant_records(
    emp: ElEmployee, staff_id: str, name: str, has_block: bool, period_start: date | None
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
        if (remarks := text(dep.trailing.get("remarks"))):
            attrs["remarks"] = remarks
        if (start := dependant_cover_start(dep, period_start)) is not None:
            attrs["effective_date"] = start.isoformat()
        if staff_id:
            attrs["employee_staff_id"] = staff_id
        if name:
            attrs["employee_name"] = name
        out.append(DependantRecord(staff_id or None, name or None, None, attrs, row=dep.row))
    return out
