"""Broker-side "employee view" preview — read-only mirrors of the portal
data endpoints, scoped through `load_employee` (tenant-checked broker access).

The preview returns exactly what the member sees: statements go through
`build_member_statement` (financials + match internals stripped), never the
raw broker statement. No member JWT is minted and nothing here mutates —
member actions (submit claim, add dependant) stay portal-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.portal_claims import build_coverage_options
from app.core.deps import load_employee
from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import active_policy_year
from app.db.session import get_db
from app.models import Claim, Dependant, Employee, MemberAccount, PolicyYear
from app.schemas.api import BenefitStatementOut, DependantOut
from app.schemas.claims import ClaimList, CoverageOptionsOut, UtilizationOut
from app.schemas.enrollment import PortalEnrollmentOut
from app.schemas.panel import ClinicSearchOut
from app.schemas.panel_card import MemberCardsOut
from app.schemas.portal import (
    MemberAccountOut,
    PortalEmployeeOut,
    PortalPolicyYearOut,
    PortalPreviewOut,
)
from app.services.claims import claim_to_out
from app.services.enrollment_elections import (
    build_portal_enrollment,
    open_window_for,
)
from app.services.member_statement import build_member_statement
from app.services.panel_cards import build_member_cards
from app.services.panel_clinics import search_policy_year_clinics
from app.services.utilization import build_utilization

router = APIRouter(
    prefix="/employees/{employee_id}/portal-preview",
    tags=["portal-preview"],
)


def _member_account_for(db: Session, employee: Employee) -> MemberAccount | None:
    """The portal account this employee would sign in with — the stamped
    binding first, then the provisioning key (client_id, staff_id)."""
    if employee.member_account_id:
        account = db.get(MemberAccount, employee.member_account_id)
        if account is not None:
            return account
    return db.execute(
        select(MemberAccount).where(
            MemberAccount.client_id == employee.client_id,
            MemberAccount.staff_id == employee.staff_id,
        )
    ).scalar_one_or_none()


@router.get("", response_model=PortalPreviewOut)
def portal_preview_context(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> PortalPreviewOut:
    """Preview header context — mirrors `GET /portal/me` for this employee,
    plus the portal-account state so the broker can see access status."""
    year = db.get(PolicyYear, employee.policy_year_id)
    active = active_policy_year(db, employee.client_id)
    account = _member_account_for(db, employee)
    return PortalPreviewOut(
        employee=PortalEmployeeOut(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        ),
        policy_year=PortalPolicyYearOut(
            id=year.id,
            year=year.year,
            start_date=year.start_date.isoformat(),
            end_date=year.end_date.isoformat(),
        )
        if year is not None
        else None,
        flex_eligible=employee.flex_tier_name is not None,
        # The live portal always resolves the ACTIVE policy year; flag when
        # this preview is looking at a different (draft/closed) year.
        is_active_policy_year=active is not None
        and active.id == employee.policy_year_id,
        member_account=(
            MemberAccountOut.model_validate(account) if account is not None else None
        ),
        enrollment_open=open_window_for(db, employee) is not None,
    )


@router.get("/benefit-statement", response_model=BenefitStatementOut)
def portal_preview_statement(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> BenefitStatementOut:
    return build_member_statement(db, employee)


@router.get("/utilization", response_model=UtilizationOut)
def portal_preview_utilization(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> UtilizationOut:
    return build_utilization(db, employee)


@router.get("/coverage-options", response_model=CoverageOptionsOut)
def portal_preview_coverage_options(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> CoverageOptionsOut:
    """Mirror of `GET /portal/coverage-options` (the member claim-form picker
    state) — same shared builder, keyed off the previewed employee's own year."""
    year = db.get(PolicyYear, employee.policy_year_id)
    statement = build_member_statement(db, employee)
    return build_coverage_options(statement, year)


@router.get("/enrollment", response_model=PortalEnrollmentOut)
def portal_preview_enrollment(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> PortalEnrollmentOut:
    """What the member sees on /portal/enrollment — read-only: unlike the
    member GET, this never materializes an enrollment row."""
    return build_portal_enrollment(db, employee)


@router.get("/dependants", response_model=list[DependantOut])
def portal_preview_dependants(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> list[DependantOut]:
    rows = db.execute(
        select(Dependant)
        .where(
            Dependant.employee_id == employee.id,
            Dependant.policy_year_id == employee.policy_year_id,
        )
        .order_by(Dependant.id)
    ).scalars().all()
    return [DependantOut.model_validate(r) for r in rows]


@router.get("/cards", response_model=MemberCardsOut)
def portal_preview_cards(
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> MemberCardsOut:
    """Mirror of `GET /portal/cards` — same resolver over the member statement,
    so the broker sees the member's cards with the same values printed.

    The member's portal-account email must be passed through: the live portal
    falls back to it when the roster has no email column, so omitting it here
    would print a blank Email / Member ID field that the member does not see.
    """
    statement = build_member_statement(db, employee)
    account = _member_account_for(db, employee)
    return MemberCardsOut(
        items=build_member_cards(
            db,
            employee,
            statement,
            member_email=account.email if account is not None else None,
        )
    )


@router.get("/clinics", response_model=ClinicSearchOut)
def portal_preview_clinics(
    clinic_type: str | None = Query(default=None, max_length=16),
    country: str | None = Query(default=None, max_length=2),
    area: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=128),
    lat: float | None = Query(default=None, ge=-90, le=90),
    lng: float | None = Query(default=None, ge=-180, le=180),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> ClinicSearchOut:
    """Mirror of `GET /portal/clinics` — same shared search, keyed off the
    previewed employee's own policy year."""
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


@router.get("/claims", response_model=ClaimList)
def portal_preview_claims(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    employee: Employee = Depends(load_employee),
    db: Session = Depends(get_db),
) -> ClaimList:
    conditions = [
        Claim.employee_id == employee.id,
        Claim.policy_year_id == employee.policy_year_id,
    ]
    total = db.scalar(select(func.count(Claim.id)).where(*conditions)) or 0
    rows = db.execute(
        select(Claim)
        .where(*conditions)
        .order_by(Claim.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return ClaimList(
        total=total,
        offset=offset,
        limit=limit,
        items=[claim_to_out(db, c) for c in rows],
    )
