"""Enrollment windows — configured enrollment periods within a policy year.

CRUD for the period itself. Opening a window (creating member enrollments from
the baseline) and closing it (deemed finalization + projection to overrides) are
lifecycle transitions handled in Phase 4.

- GET    /policy-years/{id}/enrollment-windows            — list
- POST   /policy-years/{id}/enrollment-windows            — create (draft)
- GET    /enrollment-windows/{window_id}                  — read
- PATCH  /enrollment-windows/{window_id}                  — edit (draft/open)
- DELETE /enrollment-windows/{window_id}                  — delete (draft only)

Tenant scoping rides on `load_policy_year` / `load_enrollment_window`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_enrollment_window, load_policy_year
from app.db.session import get_db
from app.models import EnrollmentWindow, PolicyYear
from app.models.enrollment_window import WindowStatus
from app.schemas.enrollment import (
    EnrollmentWindowCreate,
    EnrollmentWindowOut,
    EnrollmentWindowPatch,
    WindowCloseSummary,
    WindowOpenResult,
)
from app.services.enrollment_lifecycle import close_window, open_window

router = APIRouter(tags=["enrollment-windows"])


@router.get(
    "/policy-years/{policy_year_id}/enrollment-windows",
    response_model=list[EnrollmentWindowOut],
)
def list_windows(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> list[EnrollmentWindow]:
    rows = (
        db.execute(
            select(EnrollmentWindow)
            .where(EnrollmentWindow.policy_year_id == py.id)
            .order_by(EnrollmentWindow.opens_at.desc())
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post(
    "/policy-years/{policy_year_id}/enrollment-windows",
    response_model=EnrollmentWindowOut,
    status_code=status.HTTP_201_CREATED,
)
def create_window(
    policy_year_id: str,
    body: EnrollmentWindowCreate,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentWindow:
    window = EnrollmentWindow(
        policy_year_id=py.id,
        client_id=py.client_id,
        name=body.name,
        window_type=body.window_type,
        opens_at=body.opens_at,
        closes_at=body.closes_at,
        status=WindowStatus.draft,
        default_behavior=body.default_behavior,
        allow_plan_change=body.allow_plan_change,
        allow_leave=body.allow_leave,
        allow_dependant_changes=body.allow_dependant_changes,
        product_scope=body.product_scope,
        flex_price_source=body.flex_price_source,
        flex_drawdown_rule=body.flex_drawdown_rule,
        allow_overdraft=body.allow_overdraft,
        created_by=user.user_id,
    )
    db.add(window)
    db.flush()
    write_audit(
        db, user, action="create_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id,
        after={"policy_year_id": py.id, "name": window.name, "window_type": window.window_type},
    )
    db.commit()
    db.refresh(window)
    return window


@router.get(
    "/enrollment-windows/{window_id}",
    response_model=EnrollmentWindowOut,
)
def get_window(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
) -> EnrollmentWindow:
    return window


@router.patch(
    "/enrollment-windows/{window_id}",
    response_model=EnrollmentWindowOut,
)
def patch_window(
    window_id: str,
    body: EnrollmentWindowPatch,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentWindow:
    if window.status == WindowStatus.closed:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A closed window can no longer be edited."
        )
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(window, field, value)
    if window.opens_at >= window.closes_at:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "opens_at must be before closes_at."
        )
    db.flush()
    write_audit(
        db, user, action="update_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, after=data,
    )
    db.commit()
    db.refresh(window)
    return window


@router.post(
    "/enrollment-windows/{window_id}/open",
    response_model=WindowOpenResult,
)
def open_enrollment_window(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WindowOpenResult:
    """Open the window: create a baseline-pre-filled enrollment per active employee.

    Also the "sync new employees" action on an already-open window — idempotent,
    so re-running it only creates rows for employees that don't have one yet.
    Returns the count created so the UI can tell the broker whether the sync
    actually did anything (a re-uploaded roster reads as 0 existing + N created).
    """
    if window.status == WindowStatus.closed:
        raise HTTPException(status.HTTP_409_CONFLICT, "A closed window cannot be reopened.")
    created = open_window(db, window, user)
    db.commit()
    db.refresh(window)
    return WindowOpenResult(
        window=EnrollmentWindowOut.model_validate(window), enrollments_created=created
    )


@router.post(
    "/enrollment-windows/{window_id}/close",
    response_model=WindowCloseSummary,
)
def close_enrollment_window(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WindowCloseSummary:
    """Close the window: finalize untouched enrollments per default_behavior and
    project every enrollment's elections into effective overrides."""
    if window.status != WindowStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an open window can be closed."
        )
    summary = close_window(db, window, user)
    db.commit()
    return WindowCloseSummary(**summary)


@router.delete(
    "/enrollment-windows/{window_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_window(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    if window.status != WindowStatus.draft:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a draft window can be deleted; close it instead.",
        )
    db.delete(window)
    write_audit(
        db, user, action="delete_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, before={"name": window.name},
    )
    db.commit()
    return None
