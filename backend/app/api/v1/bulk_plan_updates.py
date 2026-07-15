"""Bulk plan updates — reassign or decline a product's plan for many members.

Preview is a read-only dry-run; apply writes the sparse overrides, records the
batch, and audits it. Apply is rate-limited like other bulk operations (10/min).

- POST /policy-years/{id}/bulk-plan-updates/preview   — dry-run, no writes
- POST /policy-years/{id}/bulk-plan-updates/apply      — apply + record
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_policy_year
from app.core.rate_limit import limiter
from app.db.base import new_uuid
from app.db.session import get_db
from app.models import BulkPlanUpdate, PolicyYear
from app.models.bulk_plan_update import BulkUpdateStatus
from app.schemas.enrollment import (
    BulkApplyResult,
    BulkPlanUpdateRequest,
    BulkPreviewResult,
)
from app.services import bulk_plan_update as svc
from app.services.enrollment_products import available_plan_codes, resolve_product_by_code
from app.services.enrollment_validation import assert_plan_available

router = APIRouter(tags=["bulk-plan-updates"])


def _resolve_product_and_validate(db: Session, py: PolicyYear, body: BulkPlanUpdateRequest):
    product = resolve_product_by_code(db, py, body.product_code)
    if product is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Product '{body.product_code}' is not configured in this policy year.",
        )
    if body.action == "set_plan":
        assert_plan_available(
            body.target_plan_code,
            available_plan_codes(db, py.id, product.id),
            body.product_code,
        )
    return product


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/preview",
    response_model=BulkPreviewResult,
)
def preview_bulk_update(
    policy_year_id: str,
    body: BulkPlanUpdateRequest,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> BulkPreviewResult:
    product = _resolve_product_and_validate(db, py, body)
    rows, counts = svc.evaluate(db, py, product, body, apply=False)
    return BulkPreviewResult(rows=rows, counts=counts)


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/apply",
    response_model=BulkApplyResult,
)
@limiter.limit("10/minute")
def apply_bulk_update(
    request: Request,
    policy_year_id: str,
    body: BulkPlanUpdateRequest,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkApplyResult:
    product = _resolve_product_and_validate(db, py, body)
    record_id = new_uuid()
    rows, counts = svc.evaluate(
        db, py, product, body, apply=True, record_id=record_id, user=user
    )
    status_value = (
        BulkUpdateStatus.applied
        if counts.get("error", 0) == 0 and counts.get("skipped", 0) == 0
        else BulkUpdateStatus.partially_failed
    )
    record = BulkPlanUpdate(
        id=record_id,
        policy_year_id=py.id,
        client_id=py.client_id,
        initiated_by=user.user_id,
        product_code=body.product_code,
        target_plan_code=body.target_plan_code,
        action=body.action,
        selector=body.selector.model_dump(),
        dependant_action=body.dependant_action.model_dump() if body.dependant_action else None,
        status=status_value,
        result_summary={"counts": counts, "rows": [r.model_dump() for r in rows]},
    )
    db.add(record)
    db.flush()
    write_audit(
        db, user, action="bulk_plan_update", entity_type="bulk_plan_update",
        entity_id=record.id, after={"product_code": body.product_code, "counts": counts},
    )
    db.commit()
    return BulkApplyResult(id=record_id, status=status_value, counts=counts, rows=rows)
