"""Flex age eligibility: age next birthday at benefit-year start, inclusive bounds.

Unknown ages stay eligible, matching existing dependant pricing/eligibility. This
is not evidence of a verified date of birth; roster correction remains necessary.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Dependant, Employee, FlexScheme, PolicyYear
from app.services.flex_pricing_resolver import (
    _dependant_eligible,
    role_age_eligible,
    scheme_dependant_age_limits,
)
from app.services.roster_attributes import age_from_attrs


def employee_age_eligible(employee: Employee, meta: dict[str, Any], ref: date | None) -> bool:
    window = meta.get("employee_age_limits")
    if not isinstance(window, dict) or ref is None:
        return True
    return role_age_eligible(
        "employee", age_from_attrs(employee.attribute_values or {}, ref), {"employee": window}
    )


def age_rule_errors(scheme: dict[str, Any]) -> list[str]:
    meta = scheme.get("meta")
    if not isinstance(meta, dict):
        return []
    errors: list[str] = []
    windows = {"Employee": meta.get("employee_age_limits")}
    dependants = meta.get("dependant_age_limits")
    if dependants is not None:
        if not isinstance(dependants, dict) or set(dependants) - {"spouse", "child"}:
            errors.append("Dependant age limits must contain only spouse and child windows.")
        else:
            windows.update({role: win for role, win in dependants.items()})
    for role, window in windows.items():
        if window is None:
            continue
        if not isinstance(window, dict) or set(window) - {"min", "max"}:
            errors.append(f"{role} age limit must be a min/max object.")
            continue
        lo, hi = window.get("min"), window.get("max")
        if any(
            value is not None and (type(value) is not int or not 0 <= value <= 150)
            for value in (lo, hi)
        ):
            errors.append(f"{role} age limits must be whole numbers from 0 to 150.")
        elif lo is not None and hi is not None and lo > hi:
            errors.append(f"{role} minimum age must not exceed maximum age.")
    return errors


def assert_flex_age_eligible(
    db: Session, claim: Claim, employee: Employee, meta: dict[str, Any]
) -> None:
    year = db.get(PolicyYear, claim.policy_year_id)
    ref = year.start_date if year else None
    if not employee_age_eligible(employee, meta, ref):
        raise HTTPException(422, "You are outside this benefit year's Flex employee age limits.")
    if claim.dependant_id and ref is not None:
        dependant = db.get(Dependant, claim.dependant_id)
        if (
            dependant is None
            or dependant.employee_id != employee.id
            or dependant.policy_year_id != claim.policy_year_id
            or dependant.client_id != employee.client_id
        ):
            raise HTTPException(422, "This dependant is not on your benefit-year record.")
        if not _dependant_eligible(dependant, scheme_dependant_age_limits(meta), ref):
            raise HTTPException(
                422, "This dependant is outside this benefit year's Flex age limits."
            )


def eligible_flex_dependant_ids(db: Session, employee: Employee, year: PolicyYear) -> list[str]:
    scheme = db.scalar(select(FlexScheme).where(FlexScheme.policy_year_id == year.id))
    bag = scheme.scheme if scheme and isinstance(scheme.scheme, dict) else {}
    meta = bag.get("meta") if isinstance(bag.get("meta"), dict) else {}
    limits = scheme_dependant_age_limits(meta)
    rows = db.scalars(
        select(Dependant).where(
            Dependant.employee_id == employee.id,
            Dependant.client_id == employee.client_id,
            Dependant.policy_year_id == year.id,
            Dependant.status == "active",
        )
    )
    return [dep.id for dep in rows if _dependant_eligible(dep, limits, year.start_date)]
