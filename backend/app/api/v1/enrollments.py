"""Per-employee enrollments — elections, leave, submit, confirm.

An enrollment is one member's session inside an open window. Brokers (on the
member's behalf) set plan elections per product, optional dependant coverage, and
a buy/sell-leave choice, then submit and confirm. Confirm projects the elections
into sparse ``EmployeePlanOverride`` rows via the lifecycle service.

The election/options/leave/submit core lives in
``app/services/enrollment_elections.py`` and is SHARED with the member portal
(``api/v1/portal_enrollment.py``) — handlers here only own broker auth + audit.

- GET  /enrollment-windows/{window_id}/enrollments       — roster (paginated)
- GET  /enrollments/{enrollment_id}                       — full detail
- GET  /enrollments/{enrollment_id}/options               — electable cohort tiers
- PUT  /enrollments/{enrollment_id}/elections             — upsert plan elections
- PUT  /enrollments/{enrollment_id}/leave                 — set buy/sell/none
- POST /enrollments/{enrollment_id}/submit                — mark submitted
- POST /enrollments/{enrollment_id}/confirm               — project to overrides
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_enrollment, load_enrollment_window
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.db.session import get_db
from app.models import (
    Employee,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    PolicyYear,
)
from app.models.enrollment import EnrollmentStatus
from app.schemas.enrollment import (
    ElectionsUpdate,
    EnrollmentOptionsOut,
    EnrollmentOut,
    EnrollmentRoster,
    EnrollmentRosterItem,
    EnrollmentSubmitIn,
    LeaveElectionIn,
)
from app.services.enrollment_elections import (
    apply_elections,
    apply_leave,
    build_enrollment_options,
    enrollment_detail,
    lock_enrollment,
    perform_submit,
    revalidate_enrollment,
)
from app.services.enrollment_flex_guard import assert_within_wallet
from app.services.enrollment_lifecycle import project_enrollment
from app.services.enrollment_validation import assert_window_accepts_edits
from app.services.underwriting import refresh_underwriting_cases

router = APIRouter(tags=["enrollments"])


@router.get(
    "/enrollment-windows/{window_id}/enrollments",
    response_model=EnrollmentRoster,
)
def list_enrollments(
    window_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    q: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
) -> EnrollmentRoster:
    base = (
        select(Enrollment, Employee.staff_id, Employee.employee_name)
        .join(Employee, Enrollment.employee_id == Employee.id)
        .where(Enrollment.window_id == window.id)
    )
    if status_filter:
        base = base.where(Enrollment.status == status_filter)
    if q:
        like = f"%{q}%"
        base = base.where(or_(Employee.staff_id.ilike(like), Employee.employee_name.ilike(like)))
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    rows = db.execute(
        base.order_by(Employee.staff_id).offset(offset).limit(limit)
    ).all()
    items = [
        EnrollmentRosterItem(
            id=enr.id, employee_id=enr.employee_id, staff_id=staff_id,
            employee_name=name, status=enr.status,
        )
        for enr, staff_id, name in rows
    ]
    return EnrollmentRoster(items=items, total=total, offset=offset, limit=limit)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentOut)
def get_enrollment(
    enrollment_id: str,
    enr: Enrollment = Depends(load_enrollment),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    return enrollment_detail(db, enr)


@router.get("/enrollments/{enrollment_id}/options", response_model=EnrollmentOptionsOut)
def get_enrollment_options(
    enrollment_id: str,
    enr: Enrollment = Depends(load_enrollment),
    db: Session = Depends(get_db),
) -> EnrollmentOptionsOut:
    """Per-product electable tiers for this member, scoped to their cohort.

    Replaces the old "every plan of the product" dropdown source: each product
    lists only the baseline tier plus the voluntary sibling tiers of the same
    cohort, direction-labelled (upgrade/downgrade).
    """
    emp = db.get(Employee, enr.employee_id)
    window = db.get(EnrollmentWindow, enr.window_id)
    return build_enrollment_options(
        db, emp, window, enr.policy_year_id, enrollment_id=enr.id
    )


@router.put("/enrollments/{enrollment_id}/elections", response_model=EnrollmentOut)
def set_elections(
    enrollment_id: str,
    body: ElectionsUpdate,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    enr = lock_enrollment(db, enr)
    apply_elections(db, enr, body.elections)
    write_audit(
        db, user, action="update_enrollment_elections", entity_type="enrollment",
        entity_id=enr.id, after={"count": len(body.elections)},
        employee_id=enr.employee_id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.put("/enrollments/{enrollment_id}/leave", response_model=EnrollmentOut)
def set_leave(
    enrollment_id: str,
    body: LeaveElectionIn,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    enr = lock_enrollment(db, enr)
    leave = apply_leave(db, enr, body)
    write_audit(
        db, user, action="update_enrollment_leave", entity_type="enrollment",
        entity_id=enr.id,
        after={"action": body.action, "days": body.days, "flex_amount": leave.flex_amount},
        employee_id=enr.employee_id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.post("/enrollments/{enrollment_id}/submit", response_model=EnrollmentOut)
def submit_enrollment(
    enrollment_id: str,
    body: EnrollmentSubmitIn | None = None,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    enr = lock_enrollment(db, enr)
    if (
        body
        and body.acknowledge_unpriced
        and user.role not in ("broker_admin", "system_admin")
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an administrator can accept unpriced elections.",
        )
    if body and body.elections is not None:
        apply_elections(db, enr, body.elections)
    if body and body.leave is not None:
        apply_leave(db, enr, body.leave)
    perform_submit(
        db, enr,
        acknowledge=bool(body and body.acknowledge_unpriced),
        actor_id=user.user_id,
    )
    write_audit(
        db, user, action="submit_enrollment", entity_type="enrollment", entity_id=enr.id,
        after={
            "elections_included": bool(body and body.elections is not None),
            "leave_included": bool(body and body.leave is not None),
        },
        employee_id=enr.employee_id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.post("/enrollments/{enrollment_id}/confirm", response_model=EnrollmentOut)
def confirm_enrollment(
    enrollment_id: str,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    enr = lock_enrollment(db, enr)
    if enr.status == EnrollmentStatus.confirmed:
        return enrollment_detail(db, enr)
    if enr.status != EnrollmentStatus.submitted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Enrollment must be submitted before it can be confirmed.",
        )
    window = db.get(EnrollmentWindow, enr.window_id)
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found.")
    assert_window_accepts_edits(window)
    revalidate_enrollment(db, enr)
    # Re-check the wallet at confirm: pricing/elections could have changed
    # between submit and confirm (unpriced acknowledgment made at submit is
    # not re-litigated here — only the hard overdraft rule is).
    assert_within_wallet(db, enr, window)
    project_enrollment(db, enr, user)
    # Projected overrides change effective SI — an elected upgrade can cross a
    # product's Non-Evidence Limit, so re-sync underwriting in the same
    # transaction (no-op unless a product carries an NEL). SCOPED to this
    # member: confirming is a per-member action, and an unscoped sync would
    # re-hydrate the whole roster (twice, with history) on every click.
    py = db.get(PolicyYear, enr.policy_year_id)
    if py is not None:
        refresh_underwriting_cases(db, py, {enr.employee_id})
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.post("/enrollments/{enrollment_id}/reopen", response_model=EnrollmentOut)
def reopen_enrollment(
    enrollment_id: str,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    """Reopen a confirmed enrollment for further plan changes, while the window
    is still open.

    Flips status ``confirmed`` → ``in_progress`` so the normal edit → submit →
    confirm flow re-enables. The already-projected overrides stay as the
    committed coverage until the broker re-submits and re-confirms (which
    re-projects). Gated to an open, in-period window — once the window closes,
    finalized coverage is changed via the coverage-revert endpoints instead.
    """
    enr = lock_enrollment(db, enr)
    if enr.status != EnrollmentStatus.confirmed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only a confirmed enrollment can be reopened.",
        )
    window = db.get(EnrollmentWindow, enr.window_id)
    if window is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found.")
    assert_window_accepts_edits(window)
    enr.status = EnrollmentStatus.in_progress
    enr.submitted_at = None
    enr.submitted_by = None
    enr.confirmed_at = None
    enr.confirmed_by = None
    db.flush()
    write_audit(
        db, user, action="reopen_enrollment", entity_type="enrollment",
        entity_id=enr.id, employee_id=enr.employee_id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.post("/enrollments/{enrollment_id}/reset", response_model=EnrollmentOut)
def reset_enrollment(
    enrollment_id: str,
    enr: Enrollment = Depends(load_enrollment),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    """Discard in-progress elections + leave so the member falls back to baseline.

    For undoing changes *before* they're materialized. Once the enrollment is
    finalized (confirmed/deemed) the effective coverage lives in overrides — use
    ``POST /employees/{id}/coverage/revert`` instead, which this returns 409 to
    steer toward.
    """
    enr = lock_enrollment(db, enr)
    if enr.status in (EnrollmentStatus.confirmed, EnrollmentStatus.deemed):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This enrollment is finalized; revert the member's coverage instead.",
        )
    window = db.get(EnrollmentWindow, enr.window_id)
    assert_window_accepts_edits(window)
    elections = db.execute(
        select(EnrollmentElection).where(EnrollmentElection.enrollment_id == enr.id)
    ).scalars().all()
    cleared = len(elections)
    for el in elections:
        db.delete(el)
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enr.id)
    ).scalar_one_or_none()
    if leave is not None:
        db.delete(leave)
    enr.status = EnrollmentStatus.not_started
    enr.submitted_at = None
    enr.submitted_by = None
    db.flush()
    write_audit(
        db, user, action="reset_enrollment", entity_type="enrollment", entity_id=enr.id,
        after={"cleared_elections": cleared}, employee_id=enr.employee_id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)
