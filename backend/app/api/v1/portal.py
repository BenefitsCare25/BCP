"""Employee-portal endpoints — scoped to the authenticated member's OWN data.

Every handler resolves the member's Employee row via
`resolve_member_employee` (their row in the active policy year) and never
accepts a client/employee id from the request — a member can only ever read
themselves. Registered in `main.py` outside the broker gate; auth is the
router-level `get_current_member` dependency.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import (
    CurrentMember,
    active_policy_year,
    get_current_member,
    resolve_member_employee,
)
from app.core.storage import get_storage
from app.db.session import get_db
from app.models import Client, Dependant, PanelCard, PolicyYearCard
from app.models.panel_card import CARD_FACES
from app.schemas.api import BenefitStatementOut, DependantOut
from app.schemas.claims import UtilizationOut
from app.schemas.panel import ClinicSearchOut
from app.schemas.panel_card import MemberCardsOut
from app.schemas.portal import (
    PortalAccessOut,
    PortalCompanyOut,
    PortalEmployeeOut,
    PortalMe,
    PortalMemberOut,
    PortalPolicyYearOut,
)
from app.services.enrollment_elections import member_window_for
from app.services.member_access import (
    Capability,
    access_for_account,
    access_payload,
)
from app.services.member_statement import build_member_statement
from app.services.panel_cards import build_member_cards
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
    client = db.get(Client, member.client_id)
    # Resolved BEFORE anything else can 404: this endpoint's job is to answer
    # for a member whose access has ended, and `access_for_account` is the only
    # resolver that copes with having no roster row in the current year (a
    # leaver after rollover, or a roster that hasn't landed yet — two states
    # that look identical from here and must not be described identically).
    access = access_for_account(
        db,
        member_account_id=member.member_account_id,
        client_id=member.client_id,
        staff_id=member.staff_id,
    )
    out = PortalMe(
        access=PortalAccessOut(**access_payload(access)),
        member=PortalMemberOut(
            id=member.member_account_id,
            email=member.email,
            staff_id=member.staff_id,
            display_name=member.display_name,
        ),
        # Resolved from the TOKEN's client, never from the request URL — that is
        # what makes it usable to check the URL against.
        company=PortalCompanyOut(
            slug=client.slug if client else None,
            name=client.name if client else "",
            legal_name=client.legal_name if client else None,
        ),
    )
    year = active_policy_year(db, member.client_id)
    if year is None:
        return out
    try:
        # UNGATED: this is the endpoint that has to tell a member their access
        # has ended, so it cannot be one of the things their access gates.
        employee = resolve_member_employee(db, member, requires=None)
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
        out.enrollment_open = member_window_for(db, employee) is not None
    return out


@router.get("/benefit-statement", response_model=BenefitStatementOut)
def portal_benefit_statement(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> BenefitStatementOut:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    return build_member_statement(db, employee)


@router.get("/utilization", response_model=UtilizationOut)
def portal_utilization(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> UtilizationOut:
    """The member's own claim usage vs limits (computed on read)."""
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
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
    employee = resolve_member_employee(db, member, requires=Capability.ENTITLEMENT)
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


@router.get("/cards", response_model=MemberCardsOut)
def portal_cards(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberCardsOut:
    """The member's panel e-cards — one per covered product, plus one per
    dependant covered under that product."""
    employee = resolve_member_employee(db, member, requires=Capability.ENTITLEMENT)
    statement = build_member_statement(db, employee)
    return MemberCardsOut(
        items=build_member_cards(db, employee, statement, member_email=member.email)
    )


@router.get("/cards/{card_id}/artwork/{face}")
def portal_card_artwork(
    card_id: str,
    face: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Response:
    """Stream card artwork to the member.

    Access is gated on the card being ASSIGNED to the member's own policy year
    — the same switch that makes the card appear in `GET /portal/cards`. An
    unassigned (or another company's) card id 404s rather than 403ing, so the
    member surface can't be used to probe the card library.
    """
    if face not in CARD_FACES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artwork not found")
    employee = resolve_member_employee(db, member, requires=Capability.ENTITLEMENT)
    assigned = db.execute(
        select(PolicyYearCard.id).where(
            PolicyYearCard.policy_year_id == employee.policy_year_id,
            PolicyYearCard.panel_card_id == card_id,
        )
    ).first()
    if assigned is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artwork not found")
    card = db.get(PanelCard, card_id)
    path = getattr(card, f"artwork_{face}_path", None) if card is not None else None
    if not path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artwork not found")
    try:
        content = get_storage().read(path)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Artwork could not be retrieved"
        ) from exc
    return Response(
        content=content,
        media_type=getattr(card, f"artwork_{face}_mime") or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/dependants", response_model=list[DependantOut])
def portal_dependants(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[DependantOut]:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    rows = db.execute(
        select(Dependant)
        .where(
            Dependant.employee_id == employee.id,
            Dependant.policy_year_id == employee.policy_year_id,
        )
        .order_by(Dependant.id)
    ).scalars().all()
    return [DependantOut.model_validate(r) for r in rows]
