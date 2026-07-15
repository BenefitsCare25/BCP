"""Employee-portal endpoints — scoped to the authenticated member's OWN data.

Every handler resolves the member's Employee row via
`resolve_member_employee` (their row in the active policy year) and never
accepts a client/employee id from the request — a member can only ever read
themselves. Registered in `main.py` outside the broker gate; auth is the
router-level `get_current_member` dependency.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import (
    CurrentMember,
    active_policy_year,
    get_current_member,
    resolve_member_employee,
)
from app.db.session import get_db
from app.models import Dependant
from app.schemas.api import BenefitStatementOut, DependantOut
from app.schemas.claims import UtilizationOut
from app.schemas.panel import ClinicSearchOut
from app.schemas.portal import (
    PortalEmployeeOut,
    PortalMe,
    PortalMemberOut,
    PortalPolicyYearOut,
)
from app.services.enrollment_elections import open_window_for
from app.services.member_statement import build_member_statement
from app.services.panel_clinics import search_policy_year_clinics
from app.services.utilization import build_utilization

router = APIRouter(
    prefix="/portal",
    tags=["portal"],
    dependencies=[Depends(get_current_member)],
)


@router.get("/me", response_model=PortalMe)
def portal_me(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> PortalMe:
    """Member profile + their coverage context. Unlike the data endpoints,
    this never 404s on a missing roster row — the shell needs it to render."""
    out = PortalMe(
        member=PortalMemberOut(
            id=member.member_account_id,
            email=member.email,
            staff_id=member.staff_id,
            display_name=member.display_name,
        )
    )
    year = active_policy_year(db, member.client_id)
    if year is None:
        return out
    try:
        employee = resolve_member_employee(db, member)
    except HTTPException:
        employee = None
    out.policy_year = PortalPolicyYearOut(
        id=year.id,
        year=year.year,
        start_date=year.start_date.isoformat(),
        end_date=year.end_date.isoformat(),
    )
    if employee is not None:
        out.employee = PortalEmployeeOut(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        )
        out.flex_eligible = employee.flex_tier_name is not None
        out.enrollment_open = open_window_for(db, employee) is not None
    return out


@router.get("/benefit-statement", response_model=BenefitStatementOut)
def portal_benefit_statement(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BenefitStatementOut:
    employee = resolve_member_employee(db, member)
    return build_member_statement(db, employee)


@router.get("/utilization", response_model=UtilizationOut)
def portal_utilization(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> UtilizationOut:
    """The member's own claim usage vs limits (computed on read)."""
    employee = resolve_member_employee(db, member)
    return build_utilization(db, employee)


@router.get("/clinics", response_model=ClinicSearchOut)
def portal_clinics(
    clinic_type: str | None = Query(default=None, max_length=16),
    country: str | None = Query(default=None, max_length=2),
    area: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=128),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClinicSearchOut:
    """Clinic locator — panel clinics tagged to the member's active policy
    year, nearest-first when the member shares their location."""
    employee = resolve_member_employee(db, member)
    return search_policy_year_clinics(
        db,
        employee.policy_year_id,
        clinic_type=clinic_type,
        country=country,
        area=area,
        q=q,
        lat=lat,
        lng=lng,
        offset=offset,
        limit=limit,
    )


@router.get("/dependants", response_model=list[DependantOut])
def portal_dependants(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[DependantOut]:
    employee = resolve_member_employee(db, member)
    rows = db.execute(
        select(Dependant)
        .where(
            Dependant.employee_id == employee.id,
            Dependant.policy_year_id == employee.policy_year_id,
        )
        .order_by(Dependant.id)
    ).scalars().all()
    return [DependantOut.model_validate(r) for r in rows]
