"""Coverage limit checks — who crosses a product's slip limits this year.

Read-only: the alerts are recomputed from the roster, matching and the saved
setup on every request, so there is nothing to keep in sync. Passing a
``product_code`` with limit values previews that product's unsaved limits
(the setup form's live check) without saving them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user
from app.db.session import get_db
from app.schemas.coverage_limits import CoverageLimitsOut, LimitOverrides
from app.services.coverage_limits import coverage_limit_alerts, limit_counts

router = APIRouter(tags=["coverage-limits"])


@router.get(
    "/policy-years/{policy_year_id}/coverage-limits",
    response_model=CoverageLimitsOut,
)
def list_coverage_limits(
    policy_year_id: str,
    product_code: str | None = Query(default=None, max_length=64),
    employee_age_limit: str | None = Query(default=None, max_length=40),
    last_entry_age: str | None = Query(default=None, max_length=40),
    spouse_age_limit: str | None = Query(default=None, max_length=40),
    child_age_limit: str | None = Query(default=None, max_length=40),
    max_sum_insured: str | None = Query(default=None, max_length=40),
    employees_above_last_entry_age: str | None = Query(default=None, max_length=4000),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CoverageLimitsOut:
    py = assert_policy_year_for_user(policy_year_id, user, db)
    supplied = {
        key: value
        for key, value in {
            "employee_age_limit": employee_age_limit,
            "last_entry_age": last_entry_age,
            "spouse_age_limit": spouse_age_limit,
            "child_age_limit": child_age_limit,
            "max_sum_insured": max_sum_insured,
            "employees_above_last_entry_age": employees_above_last_entry_age,
        }.items()
        if value is not None
    }
    overrides = LimitOverrides(**supplied) if product_code and supplied else None
    alerts = coverage_limit_alerts(
        db, py, product_code=product_code, overrides=overrides
    )
    return CoverageLimitsOut(alerts=alerts, counts=limit_counts(alerts))
