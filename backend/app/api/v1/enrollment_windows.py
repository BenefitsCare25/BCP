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

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_enrollment_window, load_policy_year
from app.db.session import get_db
from app.models import EnrollmentWindow, PolicyYear
from app.models.enrollment_window import WindowStatus
from app.schemas.enrollment import (
    BulkConfirmResult,
    CloseMemberNote,
    EnrollmentReadinessOut,
    EnrollmentWindowCreate,
    EnrollmentWindowOut,
    EnrollmentWindowPatch,
    ReadinessEmployeesOut,
    WindowCloseIn,
    WindowClosePreview,
    WindowCloseSummary,
    WindowOpenResult,
    WindowProgress,
)
from app.services.enrollment_lifecycle import (
    close_preview,
    close_window,
    confirm_submitted,
    open_window,
    window_progress,
)
from app.services.enrollment_readiness import (
    blocking_issues,
    enrollment_readiness_issues,
)
from app.services.enrollment_validation import assert_window_accepts_review
from app.services.underwriting import refresh_underwriting_cases

router = APIRouter(tags=["enrollment-windows"])

_OPEN_EDITABLE_FIELDS = {
    "name",
    "closes_at",
    "member_self_service",
    "allow_overdraft",
}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _assert_no_open_overlap(db: Session, window: EnrollmentWindow) -> None:
    db.execute(
        select(PolicyYear.id)
        .where(PolicyYear.id == window.policy_year_id)
        .with_for_update()
    ).scalar_one()
    overlap = db.execute(
        select(EnrollmentWindow.id).where(
            EnrollmentWindow.policy_year_id == window.policy_year_id,
            EnrollmentWindow.id != window.id,
            EnrollmentWindow.status == WindowStatus.open,
            EnrollmentWindow.opens_at < window.closes_at,
            EnrollmentWindow.closes_at > window.opens_at,
        )
    ).scalars().first()
    if overlap:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This enrolment period overlaps another open period in the benefit year.",
        )


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
    if body.window_type != "open":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "New-hire and life-event periods are not available until their "
            "eligibility rules are configured.",
        )
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
        member_self_service=body.member_self_service,
        uses_flex=body.uses_flex,
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


@router.get(
    "/enrollment-windows/{window_id}/readiness",
    response_model=EnrollmentReadinessOut,
)
def get_window_readiness(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> EnrollmentReadinessOut:
    issues = enrollment_readiness_issues(db, window)
    return EnrollmentReadinessOut(ready=not blocking_issues(issues), issues=issues)


@router.get(
    "/enrollment-windows/{window_id}/readiness/{issue_code}/employees",
    response_model=ReadinessEmployeesOut,
)
def get_readiness_employees(
    window_id: str,
    issue_code: str,
    q: str = Query("", max_length=255),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> ReadinessEmployeesOut:
    affected: dict[str, list[dict[str, object]]] = {}
    enrollment_readiness_issues(db, window, affected=affected)
    rows = affected.get(issue_code, [])
    needle = q.strip().casefold()
    if needle:
        rows = [r for r in rows if needle in str(r["staff_id"]).casefold()
                or needle in str(r.get("employee_name") or "").casefold()]
    rows.sort(key=lambda r: (str(r["staff_id"]), str(r["employee_id"])))
    return ReadinessEmployeesOut.model_validate({
        "total": len(rows), "offset": offset, "limit": limit,
        "items": rows[offset:offset + limit],
    })


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
            status.HTTP_409_CONFLICT, "A closed enrolment period can no longer be edited."
        )
    data = body.model_dump(exclude_unset=True)
    if window.status == WindowStatus.open:
        locked = sorted(set(data) - _OPEN_EDITABLE_FIELDS)
        if locked:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "open_window_configuration_locked",
                    "message": (
                        "Selection, pricing, and default rules are locked after "
                        "an enrolment period opens."
                    ),
                    "fields": locked,
                },
            )
    for field, value in data.items():
        setattr(window, field, value)
    # SQLite hands back the stored value naive while the patched one is aware.
    if _aware(window.opens_at) >= _aware(window.closes_at):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "opens_at must be before closes_at."
        )
    if window.status == WindowStatus.open:
        _assert_no_open_overlap(db, window)
    db.flush()
    write_audit(
        db, user, action="update_enrollment_window", entity_type="enrollment_window",
        # JSON-mode dump: the audit column is JSON and dates must be strings.
        entity_id=window.id, after=body.model_dump(mode="json", exclude_unset=True),
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
    window = db.execute(
        select(EnrollmentWindow)
        .where(EnrollmentWindow.id == window.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if window.status == WindowStatus.closed:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A closed enrolment period cannot be reopened."
        )
    _assert_no_open_overlap(db, window)
    if window.status == WindowStatus.draft:
        issues = blocking_issues(enrollment_readiness_issues(db, window))
        if issues:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "enrollment_not_ready",
                    "message": (
                        "This enrolment period is not ready to open. Resolve every "
                        "listed blocker first."
                    ),
                    "issues": issues,
                },
            )
    created = open_window(db, window, user)
    db.commit()
    db.refresh(window)
    return WindowOpenResult(
        window=EnrollmentWindowOut.model_validate(window), enrollments_created=created
    )


@router.get(
    "/enrollment-windows/{window_id}/progress",
    response_model=WindowProgress,
)
def get_window_progress(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> WindowProgress:
    return WindowProgress(**window_progress(db, window))


@router.post(
    "/enrollment-windows/{window_id}/confirm-submitted",
    response_model=BulkConfirmResult,
)
def confirm_all_submitted(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkConfirmResult:
    """Confirm every submitted enrollment in the period that passes its checks;
    report the rest by name. Works past the deadline (it is review)."""
    window = db.execute(
        select(EnrollmentWindow)
        .where(EnrollmentWindow.id == window.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    assert_window_accepts_review(window)
    confirmed, failed = confirm_submitted(db, window, user)
    if confirmed:
        py = db.get(PolicyYear, window.policy_year_id)
        if py is not None:
            refresh_underwriting_cases(db, py, set(confirmed))
    write_audit(
        db, user, action="confirm_submitted_enrollments",
        entity_type="enrollment_window", entity_id=window.id,
        after={"confirmed": len(confirmed), "failed": len(failed)},
    )
    db.commit()
    return BulkConfirmResult(
        confirmed=len(confirmed),
        failed=[CloseMemberNote(**f) for f in failed],
    )


@router.get(
    "/enrollment-windows/{window_id}/close-preview",
    response_model=WindowClosePreview,
)
def get_close_preview(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> WindowClosePreview:
    """Exactly what Close would do to each member group, including who would
    block it and whose saved choices could not be submitted for them. Runs
    close's own checks, then rolls every one of them back."""
    if window.status != WindowStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an open enrolment period can be closed."
        )
    try:
        return WindowClosePreview(**close_preview(db, window))
    finally:
        db.rollback()


@router.post(
    "/enrollment-windows/{window_id}/close",
    response_model=WindowCloseSummary,
)
def close_enrollment_window(
    window_id: str,
    body: WindowCloseIn | None = None,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WindowCloseSummary:
    """Close the window: finalize untouched enrollments per default_behavior and
    project every enrollment's elections into effective overrides."""
    window = db.execute(
        select(EnrollmentWindow)
        .where(EnrollmentWindow.id == window.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    if window.status != WindowStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an open enrolment period can be closed."
        )
    summary = close_window(
        db, window, user, submit_saved=bool(body and body.submit_saved)
    )
    # Window close projected every enrollment into overrides — elected upgrades
    # can cross a product's Non-Evidence Limit, so re-sync underwriting once
    # for the year in the same transaction (no-op without an NEL).
    py = db.get(PolicyYear, window.policy_year_id)
    if py is not None:
        refresh_underwriting_cases(db, py)
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
            "Only a draft enrolment period can be deleted; close it instead.",
        )
    db.delete(window)
    write_audit(
        db, user, action="delete_enrollment_window", entity_type="enrollment_window",
        entity_id=window.id, before={"name": window.name},
    )
    db.commit()
    return None
