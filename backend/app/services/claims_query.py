"""Shared company-scoped predicates for claims browsing and register exports."""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.clock import today
from app.models import Claim, Dependant, Employee, PolicyYear
from app.services.claim_filters import claim_insurer_filter
from app.services.log_cases import case_type_or_400
from app.services.product_insurer import placement_insurers
from app.services.roster_attributes import NAME_KEYS


def claims_conditions(
    db: Session,
    policy_year: PolicyYear,
    *,
    all_years: bool = False,
    status_filter: str | None = None,
    employee_id: str | None = None,
    case_type: str | None = None,
    search: str | None = None,
    incurred_from: date | None = None,
    incurred_to: date | None = None,
    insurer: str | None = None,
    queue: str | None = None,
    kind: str | None = None,
) -> list[ColumnElement[bool]]:
    """The caller must authorize the anchor year before using this scope."""
    if incurred_from and incurred_to and incurred_from > incurred_to:
        raise HTTPException(422, "Incurred from must not be after incurred to")
    conditions: list[ColumnElement[bool]] = [Claim.client_id == policy_year.client_id]
    if all_years:
        conditions.append(Claim.policy_year_id.in_(
            select(PolicyYear.id).where(PolicyYear.client_id == policy_year.client_id)
        ))
    else:
        conditions.append(Claim.policy_year_id == policy_year.id)
    if status_filter:
        conditions.append(Claim.status == status_filter)
    if employee_id:
        conditions.append(Claim.employee_id == employee_id)
    wanted_case_type = case_type_or_400(case_type)
    if wanted_case_type:
        conditions.append(Claim.case_type == wanted_case_type)
    if incurred_from:
        conditions.append(Claim.incurred_date >= incurred_from)
    if incurred_to:
        conditions.append(Claim.incurred_date <= incurred_to)
    if insurer:
        years = list(db.scalars(select(PolicyYear.id).where(
            PolicyYear.client_id == policy_year.client_id,
        ))) if all_years else [policy_year.id]
        conditions.append(claim_insurer_filter(insurer, placement_insurers(db, years)))
    if kind:
        conditions.append(Claim.claim_kind == kind)
    if queue == "review":
        conditions.append(Claim.status.in_(["submitted", "ai_verified", "ai_flagged"]))
    elif queue in {"insurer", "overdue"}:
        conditions.append(Claim.status == "sent_to_insurer")
        if queue == "overdue":
            conditions.append(Claim.insurer_deadline_on < today())
    if search and search.strip():
        term = search.strip()
        # Use the same fallback order as the displayed dependant name. EXISTS
        # keeps page counts stable, and autoescape treats % and _ literally.
        dependant_name = func.coalesce(*[
            func.nullif(func.trim(Dependant.attribute_values[key].as_string()), "")
            for key in NAME_KEYS
        ])
        dependant_match = select(Dependant.id).where(
            Dependant.id == Claim.dependant_id,
            Dependant.client_id == Claim.client_id,
            Dependant.policy_year_id == Claim.policy_year_id,
            dependant_name.icontains(term, autoescape=True),
        ).exists()
        conditions.append(or_(
            Claim.reference_no.icontains(term, autoescape=True),
            Claim.invoice_number.icontains(term, autoescape=True),
            Claim.provider_name.icontains(term, autoescape=True),
            Employee.staff_id.icontains(term, autoescape=True),
            Employee.employee_name.icontains(term, autoescape=True),
            dependant_match,
        ))
    return conditions
