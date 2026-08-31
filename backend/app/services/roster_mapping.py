"""Deterministic, reviewable employee-listing column mapping.

The old parser knew one hard-coded vocabulary and silently discarded every
other column. This layer resolves a workbook against system aliases, the
company's direct attribute schemas, and a previously confirmed template
profile. Unknown populated columns remain unresolved until the broker maps or
explicitly ignores them.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, EmployeeAttributeSchema, RosterMappingProfile
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.schemas.adc import RosterAttributeReadiness, RosterReadiness
from app.services.derivation_engine import derive, resolve_attribute_schemas
from app.services.roster_parser import (
    _DEPENDANT_ONLY_COLUMNS,
    _MEMBER_ID_RE,
    DEPENDANT_COLUMN_MAP,
    EMPLOYEE_COLUMN_MAP,
    _build_column_map,
    _read_sheet,
)


@dataclass(frozen=True)
class MappingAttribute:
    attribute_id: str
    display_name: str
    is_pii: bool = False
    allow_matching: bool = False
    derived: bool = False


@dataclass(frozen=True)
class ColumnDecision:
    index: int
    source_column: str
    attribute_id: str | None
    display_name: str | None
    status: str
    source: str
    non_empty_count: int


@dataclass(frozen=True)
class EmployeeMapping:
    sheet_name: str | None
    fingerprint: str | None
    digest: str | None
    reused_profile: bool
    columns: list[ColumnDecision]
    available_attributes: list[MappingAttribute]
    required_missing: list[str]

    @property
    def unresolved(self) -> bool:
        return bool(self.required_missing) or any(
            item.status == "unresolved" for item in self.columns
        )

    @property
    def parse_columns(self) -> dict[int, str | None]:
        return {
            item.index: item.attribute_id
            for item in self.columns
            if item.status != "unresolved"
            and item.attribute_id != "insurer_member_ids"
        }

    @property
    def persisted_mapping(self) -> dict[str, str | None]:
        return {
            str(item.index): item.attribute_id
            for item in self.columns
            if item.status != "unresolved"
        }


_SYSTEM_LABELS: dict[str, str] = {
    "entity": "Entity",
    "staff_id": "Staff ID",
    "employee_name": "Employee Name",
    "id_no": "Identification No. (NRIC/FIN)",
    "date_of_birth": "Date of Birth",
    "gender": "Gender",
    "marital_status": "Marital Status",
    "email": "Email Address",
    "mobile": "Mobile Phone",
    "effective_date": "Effective Date",
    "date_of_hire": "Date of Hire",
    "confirmation_date": "Confirmation Date",
    "last_day_of_service": "Last Day of Service",
    "insurer_member_ids": "Insurer Member ID",
}
_SYSTEM_PII = frozenset(
    {"employee_name", "id_no", "date_of_birth", "email", "mobile", "bank_account_no"}
)


def _normalized(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _header_fingerprint(headers: list[str]) -> str:
    payload = json.dumps([_normalized(value) for value in headers], separators=(",", ":"))
    return hashlib.sha256(f"employee|{payload}".encode()).hexdigest()


def _mapping_digest(fingerprint: str, decisions: list[ColumnDecision]) -> str:
    payload = [
        [item.index, item.attribute_id if item.status != "unresolved" else "<unresolved>"]
        for item in decisions
    ]
    return hashlib.sha256(
        f"{fingerprint}|{json.dumps(payload, separators=(',', ':'))}".encode()
    ).hexdigest()[:32]


def _attributes(schemas: list[EmployeeAttributeSchema]) -> list[MappingAttribute]:
    by_id: dict[str, MappingAttribute] = {}
    for attribute_id in sorted(set(EMPLOYEE_COLUMN_MAP.values())):
        by_id[attribute_id] = MappingAttribute(
            attribute_id=attribute_id,
            display_name=_SYSTEM_LABELS.get(
                attribute_id, attribute_id.replace("_", " ").title()
            ),
            is_pii=attribute_id in _SYSTEM_PII,
        )
    by_id["insurer_member_ids"] = MappingAttribute(
        "insurer_member_ids", "Insurer Member ID", is_pii=True
    )
    for schema in schemas:
        # A derived field has a transform and should not also be a direct import
        # target; map its source column and let the derivation remain auditable.
        derived = bool(schema.derivation_rule)
        if not derived:
            by_id[schema.attribute_id] = MappingAttribute(
                attribute_id=schema.attribute_id,
                display_name=schema.display_name,
                is_pii=schema.is_pii,
                allow_matching=schema.allow_matching,
                derived=False,
            )
    return sorted(by_id.values(), key=lambda value: value.display_name.casefold())


def _dynamic_aliases(schemas: list[EmployeeAttributeSchema]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for schema in schemas:
        if schema.derivation_rule:
            continue
        aliases.setdefault(_normalized(schema.attribute_id.replace("_", " ")), schema.attribute_id)
        aliases.setdefault(_normalized(schema.display_name), schema.attribute_id)
    return aliases


def inspect_employee_mapping(
    db: Session,
    *,
    client_id: str,
    path: Path | str,
    override: dict[str, str | None] | None = None,
) -> EmployeeMapping:
    sheet = _read_sheet(path, "Employees")
    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                (EmployeeAttributeSchema.client_id == client_id)
                | (EmployeeAttributeSchema.client_id.is_(None))
            )
        ).scalars()
    )
    attributes = _attributes(schemas)
    if sheet is None or not sheet.rows:
        return EmployeeMapping(None, None, None, False, [], attributes, [])

    raw_headers = [str(value or "").strip() for value in sheet.rows[0]]
    dependant_columns = set(
        _build_column_map(sheet.rows[0], DEPENDANT_COLUMN_MAP).values()
    )
    if any(column in dependant_columns for column in _DEPENDANT_ONLY_COLUMNS):
        return EmployeeMapping(None, None, None, False, [], attributes, [])
    fingerprint = _header_fingerprint(raw_headers)
    profile = db.execute(
        select(RosterMappingProfile).where(
            RosterMappingProfile.client_id == client_id,
            RosterMappingProfile.member_type == "employee",
            RosterMappingProfile.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    profile_mapping: dict[str, str | None] = {}
    if profile is not None:
        profile_mapping = {
            str(key): value if isinstance(value, str) else None
            for key, value in (profile.column_mapping or {}).items()
        }
    explicit = override is not None
    selected: dict[str, str | None] = override if override is not None else profile_mapping
    automatic = _build_column_map(sheet.rows[0], EMPLOYEE_COLUMN_MAP)
    dynamic_aliases = _dynamic_aliases(schemas)
    labels = {attribute.attribute_id: attribute.display_name for attribute in attributes}
    allowed = set(labels)
    decisions: list[ColumnDecision] = []
    claimed: set[str] = set()

    for index, raw_header in enumerate(raw_headers):
        non_empty = sum(
            1
            for row in sheet.rows[1:]
            if index < len(row) and row[index] not in (None, "")
        )
        key = str(index)
        attribute_id: str | None = None
        source = "unresolved"
        status = "unresolved"
        if key in selected:
            candidate = selected[key]
            if candidate is not None and candidate not in allowed:
                raise ValueError(
                    f"Column {index + 1} maps to unknown attribute {candidate!r}."
                )
            attribute_id = candidate
            source = "manual" if explicit else "saved_profile"
            status = "ignored" if candidate is None else "mapped"
        elif index in automatic:
            attribute_id = automatic[index]
            source = "known_header"
            status = "mapped"
        else:
            member_id = _MEMBER_ID_RE.match(_normalized(raw_header))
            dynamic = dynamic_aliases.get(_normalized(raw_header))
            if member_id:
                attribute_id = "insurer_member_ids"
                source = "known_header"
                status = "mapped"
            elif dynamic:
                attribute_id = dynamic
                source = "attribute_schema"
                status = "mapped"
            elif non_empty == 0:
                source = "empty_column"
                status = "ignored"

        if attribute_id and attribute_id != "insurer_member_ids":
            if attribute_id in claimed:
                raise ValueError(
                    "More than one employee column maps to "
                    f"{labels.get(attribute_id, attribute_id)}."
                )
            claimed.add(attribute_id)
        decisions.append(
            ColumnDecision(
                index=index,
                source_column=raw_header or f"Column {index + 1}",
                attribute_id=attribute_id,
                display_name=labels.get(attribute_id) if attribute_id else None,
                status=status,
                source=source,
                non_empty_count=non_empty,
            )
        )

    required_missing = ["staff_id"] if "staff_id" not in claimed else []
    return EmployeeMapping(
        sheet_name=sheet.name,
        fingerprint=fingerprint,
        digest=_mapping_digest(fingerprint, decisions),
        reused_profile=profile is not None and not explicit,
        columns=decisions,
        available_attributes=attributes,
        required_missing=required_missing,
    )


def save_employee_mapping_profile(
    db: Session,
    *,
    client_id: str,
    mapping: EmployeeMapping,
    created_by: str | None,
) -> None:
    if mapping.fingerprint is None:
        return
    profile = db.execute(
        select(RosterMappingProfile).where(
            RosterMappingProfile.client_id == client_id,
            RosterMappingProfile.member_type == "employee",
            RosterMappingProfile.fingerprint == mapping.fingerprint,
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = RosterMappingProfile(
            client_id=client_id,
            member_type="employee",
            fingerprint=mapping.fingerprint,
            source_headers=[item.source_column for item in mapping.columns],
            column_mapping=mapping.persisted_mapping,
        )
        db.add(profile)
    else:
        profile.source_headers = [item.source_column for item in mapping.columns]
        profile.column_mapping = mapping.persisted_mapping
    profile.sheet_name = mapping.sheet_name
    profile.created_by = created_by


def roster_readiness(
    db: Session, *, client_id: str, policy_year_id: str
) -> RosterReadiness:
    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                (EmployeeAttributeSchema.client_id == client_id)
                | (EmployeeAttributeSchema.client_id.is_(None))
            )
        ).scalars()
    )
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        ).scalars()
    )
    total = len(employees)
    derived_views = [derive(employee.attribute_values or {}, schemas) for employee in employees]
    attributes: list[RosterAttributeReadiness] = []
    for schema in sorted(schemas, key=lambda item: item.display_name.casefold()):
        is_derived = bool(schema.derivation_rule)
        values = []
        for employee, derived_values in zip(employees, derived_views, strict=True):
            source_values = derived_values if is_derived else (employee.attribute_values or {})
            value = source_values.get(schema.attribute_id)
            if value not in (None, ""):
                values.append(value)
        populated = len(values)
        distinct = len({_normalized(value) for value in values})
        attributes.append(
            RosterAttributeReadiness(
                attribute_id=schema.attribute_id,
                display_name=schema.display_name,
                source="derived" if is_derived else "listing",
                derived_from=schema.derived_from,
                populated_count=populated,
                missing_count=max(0, total - populated),
                coverage_percent=round(populated * 100 / total, 1) if total else 0,
                distinct_count=distinct,
                is_pii=schema.is_pii,
                allow_matching=schema.allow_matching,
                allow_ai_values=schema.allow_ai_values and not schema.is_pii,
                usable_for_matching=schema.allow_matching and populated > 0,
            )
        )
    return RosterReadiness(
        policy_year_id=policy_year_id,
        employee_count=total,
        usable_attributes=sum(item.usable_for_matching for item in attributes),
        derived_attributes=sum(
            item.source == "derived" and item.populated_count > 0
            for item in attributes
        ),
        unavailable_attributes=sum(not item.usable_for_matching for item in attributes),
        attributes=attributes,
    )
