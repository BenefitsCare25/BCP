"""Bulk plan updates — reassign or decline a product's plan for many members.

Preview is a read-only dry-run; apply writes the sparse overrides, records the
batch, and audits it. Apply is rate-limited like other bulk operations (10/min).

- POST /policy-years/{id}/bulk-plan-updates/preview   — dry-run, no writes
- POST /policy-years/{id}/bulk-plan-updates/apply      — apply + record

Both take the SAME body, and that body carries a ``MemberQuery`` — a rule, not a
list of people. Apply re-resolves the rule server-side under the preview's
``selection_digest``, so what is applied is provably the population that was
previewed, without shipping thousands of ids back and forth.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from app.services.bulk_plan_update import SelectionChanged, SelectionTooLarge
from app.services.enrollment_products import available_plan_codes, resolve_product_by_code
from app.services.enrollment_validation import assert_plan_available
from app.services.underwriting import refresh_underwriting_cases

router = APIRouter(tags=["bulk-plan-updates"])

# Rows kept inline on the stored batch record. Everything above it is summarised
# by counts + groups; a multi-megabyte JSON blob in a tenant table is not a
# record, it is a liability.
MAX_STORED_ROWS = 5000


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


def _too_large(exc: SelectionTooLarge) -> HTTPException:
    """A runaway guard, not a workflow limit — the message states how far over
    the selection is so the broker knows what to narrow."""
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        {
            "code": "selection_too_large",
            "message": (
                f"{exc.selected:,} members match this selection; the limit for one "
                f"run is {exc.limit:,}. Narrow the filters and run it in parts."
            ),
            "selected": exc.selected,
            "limit": exc.limit,
        },
    )


def _page(rows: list, offset: int, limit: int) -> list:
    return rows[offset : offset + limit]


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/preview",
    response_model=BulkPreviewResult,
)
def preview_bulk_update(
    policy_year_id: str,
    body: BulkPlanUpdateRequest,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> BulkPreviewResult:
    product = _resolve_product_and_validate(db, py, body)
    try:
        result = svc.evaluate(db, py, product, body, apply=False)
    except SelectionTooLarge as exc:
        raise _too_large(exc) from exc
    return BulkPreviewResult(
        rows=_page(result.rows, offset, limit),
        rows_total=len(result.rows),
        rows_offset=offset,
        counts=result.counts,
        groups=result.groups,
        impact=result.impact,
        selection_digest=result.digest,
    )


@router.post(
    "/policy-years/{policy_year_id}/bulk-plan-updates/apply",
    response_model=BulkApplyResult,
)
@limiter.limit("10/minute")
def apply_bulk_update(
    request: Request,
    policy_year_id: str,
    body: BulkPlanUpdateRequest,
    limit: int = Query(100, ge=1, le=1000),
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkApplyResult:
    product = _resolve_product_and_validate(db, py, body)
    record_id = new_uuid()

    # The digest is verified inside `evaluate`, BEFORE the first write — the
    # population (or the coverage of someone in it) may have moved since the
    # broker approved the preview, and an apply that trips this must leave
    # nothing behind.
    try:
        result = svc.evaluate(
            db, py, product, body, apply=True, record_id=record_id, user=user,
            expected_digest=body.selection_digest,
        )
    except SelectionTooLarge as exc:
        raise _too_large(exc) from exc
    except SelectionChanged as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "code": "selection_changed",
                "message": (
                    "The roster changed since this preview — re-run the preview "
                    "and check the numbers before applying."
                ),
                "selection_digest": exc.digest,
            },
        ) from exc

    counts = result.counts
    applied_ids = {
        r.employee_id for r in result.rows if r.outcome == "applied" and r.employee_id
    }
    if applied_ids:
        # A plan change moves eligible sum insured, which is exactly what the
        # NEL gates key on. Scoped to the batch's members per the underwriting
        # invariant (an unscoped run hydrates the whole roster twice AND would
        # retire cases for households it never recomputed). Flush-only — the
        # commit below owns it, so a fault rolls the whole batch back.
        refresh_underwriting_cases(db, py, applied_ids)

    # Only an ERROR is a failure. `skipped` means "this member isn't enrolled in
    # the product", which is the NORMAL result of a roster-wide rule — "move all
    # of Sales to Plan 2" necessarily sweeps in people the product doesn't
    # cover. Counting it as a partial failure (correct when the only selectors
    # were explicit ids) would file every filter-driven run as partially_failed
    # and make the status worthless.
    status_value = (
        BulkUpdateStatus.applied
        if counts.get("error", 0) == 0
        else BulkUpdateStatus.partially_failed
    )
    stored = result.rows[:MAX_STORED_ROWS]
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
        result_summary={
            "counts": counts,
            "groups": [g.model_dump() for g in result.groups],
            "impact": result.impact.model_dump(),
            "rows": [r.model_dump() for r in stored],
            "rows_total": len(result.rows),
            "rows_truncated": len(result.rows) > len(stored),
        },
    )
    db.add(record)
    db.flush()
    write_audit(
        db, user, action="bulk_plan_update", entity_type="bulk_plan_update",
        entity_id=record.id, after={"product_code": body.product_code, "counts": counts},
    )
    db.commit()
    return BulkApplyResult(
        id=record_id,
        status=status_value,
        counts=counts,
        rows=_page(result.rows, 0, limit),
        rows_total=len(result.rows),
        groups=result.groups,
        impact=result.impact,
    )
