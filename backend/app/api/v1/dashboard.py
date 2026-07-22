"""Firm-level dashboard summary.

`GET /dashboard/summary` powers the firm Home page: one call returns every
company the caller can act on, each with a handful of headline counts, plus a
firm-wide roll-up. Scoped to `accessible_clients` (the same firm boundary every
other endpoint respects), so it can't leak another firm's companies.

Counts key off each company's CURRENT benefit year (`status == active`) — the
one the portal reads and claims submit against. Companies with no active year
report null year and zero counts. The heavy lifting is a few GROUPED queries
over the union of current-year ids rather than N queries per company.

Postgres note: on multi-firm `system_admin` sessions the request runs inside a
single firm schema (the active client's), so counts for companies in OTHER
firms are not visible here — consistent with system_admin operating one firm at
a time. On SQLite (single schema) every accessible company is counted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.identity import accessible_clients
from app.db.session import get_db
from app.models.claim import (
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
    Claim,
)
from app.models.dependant import Dependant
from app.models.employee import Employee
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.models.policy_year import PolicyYear, PolicyYearStatus

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Claims awaiting a broker decision (AI done or routed to manual). Excludes
# in-flight (ai_review_pending), member-side (needs_info), and terminal states.
_CLAIMS_TO_REVIEW = (
    CLAIM_STATUS_SUBMITTED,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_AI_FLAGGED,
)


class CompanyYear(BaseModel):
    id: str
    year: int
    status: str


class CompanySummary(BaseModel):
    id: str
    name: str
    current_year: CompanyYear | None
    member_count: int
    dependant_count: int
    claims_to_review: int
    enrollment_open: bool


class FirmTotals(BaseModel):
    company_count: int
    member_count: int
    dependant_count: int
    claims_to_review: int
    windows_open: int


class DashboardSummary(BaseModel):
    firm: FirmTotals
    companies: list[CompanySummary]


def _grouped_count(db: Session, column, model, year_ids: list[str], *filters) -> dict[str, int]:
    """`{policy_year_id: count}` for `year_ids`, applying extra WHERE filters."""
    if not year_ids:
        return {}
    stmt = (
        select(column, func.count())
        .where(column.in_(year_ids), *filters)
        .group_by(column)
    )
    return {row[0]: row[1] for row in db.execute(stmt).all()}


@router.get("/summary", response_model=DashboardSummary)
def get_summary(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    clients = accessible_clients(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        db=db,
    )
    client_ids = [c.id for c in clients]

    # Current (active) benefit year per company. If more than one is active,
    # take the most recent by start_date — the one Home should headline.
    current_year_by_client: dict[str, PolicyYear] = {}
    if client_ids:
        rows = db.execute(
            select(PolicyYear)
            .where(
                PolicyYear.client_id.in_(client_ids),
                PolicyYear.status == PolicyYearStatus.active,
            )
            .order_by(PolicyYear.client_id, PolicyYear.start_date.desc())
        ).scalars()
        for py in rows:
            current_year_by_client.setdefault(py.client_id, py)

    year_ids = [py.id for py in current_year_by_client.values()]

    members = _grouped_count(
        db, Employee.policy_year_id, Employee, year_ids, Employee.status == "active"
    )
    dependants = _grouped_count(
        db, Dependant.policy_year_id, Dependant, year_ids, Dependant.status == "active"
    )
    claims = _grouped_count(
        db, Claim.policy_year_id, Claim, year_ids, Claim.status.in_(_CLAIMS_TO_REVIEW)
    )
    open_windows: set[str] = set()
    if year_ids:
        open_windows = {
            row[0]
            for row in db.execute(
                select(EnrollmentWindow.policy_year_id).where(
                    EnrollmentWindow.policy_year_id.in_(year_ids),
                    EnrollmentWindow.status == WindowStatus.open,
                )
            ).all()
        }

    companies: list[CompanySummary] = []
    for client in clients:
        py = current_year_by_client.get(client.id)
        yid = py.id if py else None
        companies.append(
            CompanySummary(
                id=client.id,
                name=client.name,
                current_year=(
                    CompanyYear(id=py.id, year=py.year, status=py.status.value)
                    if py
                    else None
                ),
                member_count=members.get(yid, 0) if yid else 0,
                dependant_count=dependants.get(yid, 0) if yid else 0,
                claims_to_review=claims.get(yid, 0) if yid else 0,
                enrollment_open=yid in open_windows if yid else False,
            )
        )

    firm = FirmTotals(
        company_count=len(companies),
        member_count=sum(c.member_count for c in companies),
        dependant_count=sum(c.dependant_count for c in companies),
        claims_to_review=sum(c.claims_to_review for c in companies),
        windows_open=sum(1 for c in companies if c.enrollment_open),
    )
    return DashboardSummary(firm=firm, companies=companies)
