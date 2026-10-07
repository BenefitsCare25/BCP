"""Tenant-resolved employee attribute catalog used for matching."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Employee, EmployeeAttributeSchema
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.services.derivation_engine import derive, resolve_attribute_schemas
from app.services.eligibility_mapping.base import AttributeValueCatalog


def build_attribute_catalog(
    db: Session, policy_year_id: str, client_id: str
) -> tuple[AttributeValueCatalog, list[Employee], list[dict[str, Any]]]:
    """Build the internal, tenant-resolved matching vocabulary and eval views.

    This never leaves our service boundary. ``allow_matching`` controls what
    rules may evaluate; PII and ``allow_ai_values`` independently control what
    can be included in an external AI request below.
    """

    schemas = resolve_attribute_schemas(
        db.execute(
            select(EmployeeAttributeSchema).where(
                tenant_or_global(EmployeeAttributeSchema.client_id, client_id)
            )
        ).scalars()
    )
    matching_schemas = [schema for schema in schemas if schema.allow_matching]
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.client_id == client_id,
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        ).scalars()
    )
    views = [
        {
            **(employee.attribute_values or {}),
            **derive(employee.attribute_values or {}, matching_schemas),
        }
        for employee in employees
    ]

    values: dict[str, list[Any]] = {}
    configured_values: dict[str, list[Any]] = {}
    populated: dict[str, int] = {}
    data_types: dict[str, str] = {}
    for schema in matching_schemas:
        attribute_id = schema.attribute_id
        data_types[attribute_id] = schema.data_type
        configured_values[attribute_id] = list(schema.enum_values or [])
        distinct: list[Any] = []
        seen: set[str] = set()
        count = 0
        for view in views:
            value = view.get(attribute_id)
            if value in (None, ""):
                continue
            count += 1
            key = str(value).strip().casefold()
            if key not in seen:
                seen.add(key)
                distinct.append(value)
        populated[attribute_id] = count
        # With no/currently-empty roster field, configured enum values still let
        # us compile a proposal, but ``populated`` remains zero so validation
        # does not confuse catalog vocabulary with current matching evidence.
        if not distinct and schema.enum_values:
            distinct = list(schema.enum_values)
        values[attribute_id] = distinct

    return (
        AttributeValueCatalog(
            values=values,
            data_types=data_types,
            populated=populated,
            employee_count=len(employees),
            roster_present=bool(employees),
            configured_values=configured_values,
        ),
        employees,
        views,
    )
