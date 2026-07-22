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

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.identity import accessible_clients
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.category import Category
from app.models.claim import (
    CLAIM_STATUS_AI_FLAGGED,
    CLAIM_STATUS_AI_VERIFIED,
    CLAIM_STATUS_SUBMITTED,
    Claim,
)
from app.models.dependant import DEPENDANT_STATUS_PENDING, Dependant
from app.models.employee import Employee
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.models.policy_year import PolicyYear, PolicyYearStatus
from app.models.underwriting_case import UnderwritingCase, UnderwritingStatus

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
    dependants_pending: int
    employees_unmatched: int
    matching_stale: bool
    underwriting_pending: int
    enrollment_open: bool
    enrollment_closes_at: datetime | None


class FirmTotals(BaseModel):
    company_count: int
    member_count: int
    dependant_count: int
    claims_to_review: int
    dependants_pending: int
    employees_unmatched: int
    underwriting_pending: int
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
    # Portal self-added dependants awaiting a broker approval decision.
    deps_pending = _grouped_count(
        db, Dependant.policy_year_id, Dependant, year_ids,
        Dependant.status == DEPENDANT_STATUS_PENDING,
    )
    # Members above free-cover limit still awaiting the insurer's U/W decision.
    uw_pending = _grouped_count(
        db, UnderwritingCase.policy_year_id, UnderwritingCase, year_ids,
        UnderwritingCase.status == UnderwritingStatus.pending,
    )

    unmatched = _unmatched_by_year(db, year_ids)
    stale_years = _stale_matching_years(db, year_ids)
    window_close = _open_window_close_by_year(db, year_ids)

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
                dependants_pending=deps_pending.get(yid, 0) if yid else 0,
                employees_unmatched=unmatched.get(yid, 0) if yid else 0,
                matching_stale=yid in stale_years if yid else False,
                underwriting_pending=uw_pending.get(yid, 0) if yid else 0,
                enrollment_open=yid in window_close if yid else False,
                enrollment_closes_at=window_close.get(yid) if yid else None,
            )
        )

    firm = FirmTotals(
        company_count=len(companies),
        member_count=sum(c.member_count for c in companies),
        dependant_count=sum(c.dependant_count for c in companies),
        claims_to_review=sum(c.claims_to_review for c in companies),
        dependants_pending=sum(c.dependants_pending for c in companies),
        employees_unmatched=sum(c.employees_unmatched for c in companies),
        underwriting_pending=sum(c.underwriting_pending for c in companies),
        windows_open=sum(1 for c in companies if c.enrollment_open),
    )
    return DashboardSummary(firm=firm, companies=companies)


def _unmatched_by_year(db: Session, year_ids: list[str]) -> dict[str, int]:
    """`{policy_year_id: employees with no matched category}`.

    Counts ALL employees (matched = non-null `matched_category_id`), exactly as
    the match-results page does (`matches.py`). This dashboard number is the
    entry point to that page, so the two MUST agree — filtering to active only
    here would headline a smaller count than the page the broker lands on.
    """
    if not year_ids:
        return {}
    rows = db.execute(
        select(
            Employee.policy_year_id,
            func.count(Employee.id),
            func.count(Employee.matched_category_id),
        )
        .where(Employee.policy_year_id.in_(year_ids))
        .group_by(Employee.policy_year_id)
    ).all()
    return {yid: (total or 0) - (matched or 0) for yid, total, matched in rows}


def _stale_matching_years(db: Session, year_ids: list[str]) -> set[str]:
    """Years whose categories changed AFTER the last matching run.

    Same staleness rule as `matches.py`: a category re-parse / rule edit / tier
    change bumps `Category.updated_at` but matched-category snapshots don't
    self-heal, so stored matches silently drift. "Never run" is NOT flagged here
    — the unmatched count already carries that case.
    """
    if not year_ids:
        return set()
    cat_updated = {
        yid: ts
        for yid, ts in db.execute(
            select(Category.policy_year_id, func.max(Category.updated_at))
            .where(Category.policy_year_id.in_(year_ids))
            .group_by(Category.policy_year_id)
        ).all()
    }
    last_run = {
        eid: ts
        for eid, ts in db.execute(
            select(AuditLog.entity_id, func.max(AuditLog.created_at))
            .where(
                AuditLog.action == "run_matching",
                AuditLog.entity_type == "policy_year",
                AuditLog.entity_id.in_(year_ids),
            )
            .group_by(AuditLog.entity_id)
        ).all()
    }
    return {
        yid
        for yid, changed in cat_updated.items()
        if changed is not None
        and last_run.get(yid) is not None
        and changed > last_run[yid]
    }


def _open_window_close_by_year(
    db: Session, year_ids: list[str]
) -> dict[str, datetime]:
    """`{policy_year_id: earliest closes_at}` for years with an OPEN window.

    A year can hold several open windows; the nearest close date is the one the
    dashboard headlines. Membership in this dict also drives `enrollment_open`.
    """
    if not year_ids:
        return {}
    rows = db.execute(
        select(
            EnrollmentWindow.policy_year_id,
            func.min(EnrollmentWindow.closes_at),
        )
        .where(
            EnrollmentWindow.policy_year_id.in_(year_ids),
            EnrollmentWindow.status == WindowStatus.open,
        )
        .group_by(EnrollmentWindow.policy_year_id)
    ).all()
    return {yid: closes for yid, closes in rows}
