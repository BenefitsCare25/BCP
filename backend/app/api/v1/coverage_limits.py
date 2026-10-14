"""Coverage limit checks — who crosses a product's slip limits this year.

The alerts are recomputed from the roster, matching and the saved setup on
every request, so there is nothing to keep in sync. Passing a
``product_code`` with limit values previews that product's unsaved limits
(the setup form's live check) without saving them. The one write is the
broker's keep/exclude decision on a dependant above an age limit.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_editable, assert_policy_year_for_user
from app.db.session import get_db
from app.models import Dependant, DependantCoverDecision, PolicyYear, Product
from app.models.dependant_cover_decision import DECISION_KEEP
from app.schemas.coverage_limits import (
    EXEMPT_LIST_MAX,
    LIMIT_TEXT_MAX,
    CoverageLimitsOut,
    DependantCoverDecisionIn,
    DependantCoverDecisionOut,
    LimitOverrides,
)
from app.services.coverage_limits import coverage_limit_alerts, limit_counts
from app.services.enrollment_products import resolve_product_by_code

router = APIRouter(tags=["coverage-limits"])


@router.get(
    "/policy-years/{policy_year_id}/coverage-limits",
    response_model=CoverageLimitsOut,
)
def list_coverage_limits(
    policy_year_id: str,
    product_code: str | None = Query(default=None, max_length=64),
    employee_age_limit: str | None = Query(default=None, max_length=LIMIT_TEXT_MAX),
    last_entry_age: str | None = Query(default=None, max_length=LIMIT_TEXT_MAX),
    spouse_age_limit: str | None = Query(default=None, max_length=LIMIT_TEXT_MAX),
    child_age_limit: str | None = Query(default=None, max_length=LIMIT_TEXT_MAX),
    max_sum_insured: str | None = Query(default=None, max_length=LIMIT_TEXT_MAX),
    employees_above_last_entry_age: str | None = Query(
        default=None, max_length=EXEMPT_LIST_MAX
    ),
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


def _assert_can_decide(user: CurrentUser) -> None:
    if user.role == "broker_viewer" or user.platform_access == "read":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access cannot change cover.")


def _decision_target(
    db: Session, py: PolicyYear, dependant_id: str, product_code: str
) -> tuple[Dependant, Product]:
    dependant = db.get(Dependant, dependant_id)
    if dependant is None or dependant.policy_year_id != py.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found in this benefit year.")
    product = resolve_product_by_code(db, py, product_code)
    if product is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Product '{product_code}' is not configured in this benefit year.",
        )
    return dependant, product


def _snapshot(row: DependantCoverDecision) -> dict[str, Any]:
    return {
        "dependant_id": row.dependant_id,
        "product_id": row.product_id,
        "decision": row.decision,
        "reason": row.reason,
    }


@router.put(
    "/policy-years/{policy_year_id}/dependant-cover-decisions",
    response_model=DependantCoverDecisionOut,
)
def set_dependant_cover_decision(
    policy_year_id: str,
    body: DependantCoverDecisionIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DependantCoverDecisionOut:
    """Keep a dependant above an age limit covered, or exclude them from the
    product. Excluding takes them off that product's cover everywhere."""
    _assert_can_decide(user)
    py = assert_policy_year_editable(assert_policy_year_for_user(policy_year_id, user, db))
    dependant, product = _decision_target(db, py, body.dependant_id, body.product_code)
    reason = (body.reason or "").strip() or None
    if body.decision == DECISION_KEEP and reason is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Say why the dependant stays covered, such as full-time study.",
        )
    row = db.scalar(
        select(DependantCoverDecision).where(
            DependantCoverDecision.policy_year_id == py.id,
            DependantCoverDecision.dependant_id == dependant.id,
            DependantCoverDecision.product_id == product.id,
        )
    )
    before = _snapshot(row) if row is not None else None
    if row is None:
        row = DependantCoverDecision(
            client_id=py.client_id,
            policy_year_id=py.id,
            dependant_id=dependant.id,
            product_id=product.id,
            decision=body.decision,
        )
        db.add(row)
    row.decision = body.decision
    row.reason = reason
    row.decided_by = user.user_id
    db.flush()
    write_audit(
        db, user, action="set_dependant_cover_decision",
        entity_type="dependant_cover_decision", entity_id=row.id,
        before=before, after=_snapshot(row), employee_id=dependant.employee_id,
    )
    db.commit()
    return DependantCoverDecisionOut(
        dependant_id=dependant.id, product_code=product.code,
        decision=row.decision, reason=row.reason,
    )


@router.delete(
    "/policy-years/{policy_year_id}/dependant-cover-decisions/{dependant_id}/{product_code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_dependant_cover_decision(
    policy_year_id: str,
    dependant_id: str = Path(max_length=36),
    product_code: str = Path(max_length=64),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Undo a decision: the dependant is back to needing one. Idempotent."""
    _assert_can_decide(user)
    py = assert_policy_year_editable(assert_policy_year_for_user(policy_year_id, user, db))
    dependant, product = _decision_target(db, py, dependant_id, product_code)
    row = db.scalar(
        select(DependantCoverDecision).where(
            DependantCoverDecision.policy_year_id == py.id,
            DependantCoverDecision.dependant_id == dependant.id,
            DependantCoverDecision.product_id == product.id,
        )
    )
    if row is None:
        return None
    before = _snapshot(row)
    db.delete(row)
    write_audit(
        db, user, action="delete_dependant_cover_decision",
        entity_type="dependant_cover_decision", entity_id=row.id,
        before=before, employee_id=dependant.employee_id,
    )
    db.commit()
    return None
