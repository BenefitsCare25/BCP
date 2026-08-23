"""Leave policy — buy/sell-leave configuration for a policy year.

One policy per year, upsert-by-year (like the Flex scheme). Tracks the buy/sell
bounds + increment AND the per-day price tag (``leave_rates``, keyed by one
grade/designation attribute — see ``services/leave_pricing_resolver``), whose
signed flex impact is snapshotted onto each ``LeaveElection``.

- GET /policy-years/{id}/leave-rate-options — grade/designation vocabulary
- GET /policy-years/{id}/leave-policy   — read (404 if unset)
- PUT /policy-years/{id}/leave-policy    — upsert

Tenant scoping rides on `load_policy_year`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year
from app.db.session import get_db
from app.models import Employee, LeavePolicy, PolicyYear
from app.schemas.enrollment import LeavePolicyOut, LeavePolicyUpsert, LeaveRateOptions
from app.services.enrollment_validation import assert_enrollment_config_editable
from app.services.leave_pricing_resolver import (
    build_leave_rate_options,
    validate_leave_rates_shape,
)

router = APIRouter(tags=["leave-policy"])


@router.get(
    "/policy-years/{policy_year_id}/leave-rate-options",
    response_model=LeaveRateOptions,
)
def get_leave_rate_options(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> LeaveRateOptions:
    """Grade/designation attributes + distinct roster values, for the rate grid."""
    employees = list(
        db.execute(
            select(Employee).where(Employee.policy_year_id == py.id)
        ).scalars().all()
    )
    return LeaveRateOptions(**build_leave_rate_options(employees))


@router.get(
    "/policy-years/{policy_year_id}/leave-policy",
    response_model=LeavePolicyOut,
)
def get_leave_policy(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> LeavePolicy:
    policy = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == py.id)
    ).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No leave policy configured.")
    return policy


@router.put(
    "/policy-years/{policy_year_id}/leave-policy",
    response_model=LeavePolicyOut,
)
def upsert_leave_policy(
    policy_year_id: str,
    body: LeavePolicyUpsert,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LeavePolicy:
    errs = validate_leave_rates_shape(
        body.leave_rates,
        min_buy_days=body.min_buy_days,
        min_sell_days=body.min_sell_days,
    )
    if errs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "; ".join(errs))
    assert_enrollment_config_editable(db, py.id, "The leave policy")
    policy = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == py.id)
    ).scalar_one_or_none()
    action = "update_leave_policy"
    if policy is None:
        policy = LeavePolicy(policy_year_id=py.id, client_id=py.client_id)
        db.add(policy)
        action = "set_leave_policy"
    for field, value in body.model_dump().items():
        setattr(policy, field, value)
    db.flush()
    write_audit(
        db, user, action=action, entity_type="leave_policy",
        entity_id=policy.id, after=body.model_dump(),
    )
    db.commit()
    db.refresh(policy)
    return policy
