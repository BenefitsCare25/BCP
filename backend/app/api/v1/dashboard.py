"""Today's portfolio roster and unresolved work across accessible benefit years."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.clock import today as business_today
from app.core.deps import assert_policy_year_for_user
from app.core.identity import accessible_clients
from app.db.session import get_db
from app.models.client import Client
from app.models.policy_year import PolicyYear
from app.schemas.dashboard import CompanySummary, CompanyYear, DashboardSummary, FirmTotals
from app.services.dashboard_counts import (
    _CLAIMS_TO_REVIEW,  # noqa: F401 - compatibility for existing callers
    _open_window_close_by_year,  # noqa: F401 - compatibility for existing callers
    collect_counts,
)
from app.services.product_insurer import placement_insurers

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
_COUNT_FIELDS = (
    "member_count", "dependant_count", "claims_to_review", "verification_pending",
    "insured_claims_to_review", "wallet_claims_to_review", "claims_with_insurer",
    "claims_overdue", "messages_awaiting_reply", "dependants_pending",
    "employees_unmatched", "underwriting_pending",
)


def _current_periods(years: list[PolicyYear], today: date) -> dict[str, PolicyYear]:
    selected: dict[str, PolicyYear] = {}
    for year in years:
        if year.start_date <= today <= year.end_date:
            selected.setdefault(year.client_id, year)
    for year in reversed(years):
        if year.start_date > today:
            selected.setdefault(year.client_id, year)
    for year in years:
        selected.setdefault(year.client_id, year)
    return selected


def _company_year(year: PolicyYear | None) -> CompanyYear | None:
    return None if year is None else CompanyYear(
        id=year.id, year=year.year, status=year.status.value,
        start_date=year.start_date, end_date=year.end_date,
    )


def _company_summary(
    client: Client, year: PolicyYear | None, counts: dict[str, Any],
    today: date, next_year: PolicyYear | None = None, restrict_matching: bool = False,
) -> CompanySummary:
    year_id = year.id if year else None
    values = {name: counts.get(name, {}).get(year_id, 0) for name in _COUNT_FIELDS}
    values.update(counts["claims"].get(year_id, {}))
    stale = year_id in counts["stale_years"]
    # Historic matching is a roster snapshot, rather than an unresolved workflow.
    if restrict_matching and year and year.end_date < today:
        values["employees_unmatched"] = 0
        stale = False
    return CompanySummary(
        id=client.id, name=client.name, current_year=_company_year(year),
        next_year=_company_year(next_year), **values, matching_stale=stale,
        **counts["windows"].get(year_id, {
            "enrollment_open": False, "enrollment_closes_at": None,
        }),
    )


def _has_work(company: CompanySummary) -> bool:
    return any(getattr(company, name) for name in _COUNT_FIELDS[2:]) or any((
        company.matching_stale, company.enrollment_open,
        company.enrollment_scheduled, company.enrollment_overdue,
    ))


def _totals(
    companies: list[CompanySummary], work: list[CompanySummary], today: date,
) -> FirmTotals:
    roster = [company for company in companies if company.current_year
              and company.current_year.start_date <= today <= company.current_year.end_date]
    values = {name: sum(getattr(company, name) for company in work)
              for name in _COUNT_FIELDS[2:]}
    return FirmTotals(
        company_count=len(companies),
        member_count=sum(company.member_count for company in roster),
        dependant_count=sum(company.dependant_count for company in roster),
        windows_open=len({company.id for company in work if company.enrollment_open}),
        **values,
    )


@router.get("/summary", response_model=DashboardSummary)
def get_summary(
    policy_year_id: str | None = Query(default=None),
    insurer: str | None = Query(default=None, max_length=128),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DashboardSummary:
    clients = accessible_clients(
        role=user.role, broker_firm_id=user.broker_firm_id, user_id=user.user_id, db=db,
    )
    years = list(db.scalars(select(PolicyYear).where(
        PolicyYear.client_id.in_([client.id for client in clients]),
    ).order_by(PolicyYear.client_id, PolicyYear.start_date.desc(), PolicyYear.id)))
    today = business_today()
    selected = _current_periods(years, today)
    if policy_year_id is not None:
        year = assert_policy_year_for_user(policy_year_id, user, db)
        selected[year.client_id] = year
    placements = placement_insurers(db, [year.id for year in years])
    current_ids = {year.id for year in selected.values()}
    labels = {label for (year_id, _), label in placements.items() if year_id in current_ids}
    insurer_name = insurer.strip().casefold() if insurer and insurer.strip() else None
    if insurer_name:
        matching_ids = {year_id for (year_id, _), label in placements.items()
                        if label.casefold() == insurer_name}
        clients = [client for client in clients if client.id in selected
                   and selected[client.id].id in matching_ids]
    client_ids = {client.id for client in clients}
    work_years = [year for year in years if year.client_id in client_ids
                  and (not insurer_name or year.id in current_ids)
                  and (policy_year_id is None or year.id == selected[year.client_id].id)]
    count_ids = list({year.id for year in work_years} | {
        year.id for client_id, year in selected.items() if client_id in client_ids
    })
    counts = collect_counts(db, count_ids, today, insurer_name, placements)
    by_client = {client.id: client for client in clients}
    companies = []
    for client in clients:
        current = selected.get(client.id)
        future = [year for year in years if year.client_id == client.id
                  and current and year.start_date > current.end_date]
        companies.append(_company_summary(
            client, current, counts, today,
            min(future, key=lambda year: year.start_date) if future else None,
        ))
    work = [_company_summary(by_client[year.client_id], year, counts, today, restrict_matching=True)
            for year in work_years]
    work = [company for company in work if _has_work(company)]
    return DashboardSummary(
        firm=_totals(companies, work, today), companies=companies, work_by_year=work,
        insurers=sorted(labels, key=str.casefold), business_date=today,
    )
