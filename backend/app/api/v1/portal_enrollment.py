"""Member-facing enrollment — the portal's own election surface.

During an open, in-period ``EnrollmentWindow`` a member can upgrade/downgrade
their plan tier, decline voluntary cover, adjust dependant coverage, and trade
leave — exactly the broker-on-behalf flow in ``api/v1/enrollments.py``, running
through the SAME shared core (``services/enrollment_elections.py``) so the two
surfaces cannot diverge in validation or pricing.

Differences from the broker surface, by design:

- Scoping: the member's Employee row via ``resolve_member_employee`` — no
  enrollment id is ever accepted from the request.
- Finalized enrollments (confirmed/deemed) are read-only for members; only a
  broker can reopen.
- Members submit; the broker confirms (projection into overrides stays a
  broker/close-window action).
- Audit rows are written with ``write_member_audit`` (actor_type="member").

Registered in ``main.py`` OUTSIDE the broker gate.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.portal_auth import (
    CurrentMember,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Employee, Enrollment, EnrollmentWindow
from app.models.enrollment import EnrollmentStatus
from app.schemas.enrollment import (
    ElectionsUpdate,
    EnrollmentOut,
    EnrollmentSubmitIn,
    LeaveElectionIn,
    PortalEnrollmentOut,
)
from app.services.enrollment_elections import (
    apply_elections,
    apply_leave,
    build_portal_enrollment,
    enrollment_detail,
    find_enrollment,
    open_window_for,
    perform_submit,
)
from app.services.enrollment_lifecycle import baseline_for

router = APIRouter(
    prefix="/portal/enrollment",
    tags=["portal-enrollment"],
    dependencies=[Depends(get_current_member)],
)

_FINAL_STATUSES = (EnrollmentStatus.confirmed, EnrollmentStatus.deemed)


def _get_or_create_enrollment(
    db: Session, window: EnrollmentWindow, employee: Employee
) -> Enrollment:
    """The member's enrollment in this window. ``open_window`` pre-creates rows
    for everyone active at open; a member added afterwards gets theirs lazily
    here — same baseline snapshot, so deemed behavior at close still works."""
    enr = find_enrollment(db, window, employee)
    if enr is not None:
        return enr
    enr = Enrollment(
        window_id=window.id,
        policy_year_id=window.policy_year_id,
        client_id=window.client_id,
        employee_id=employee.id,
        status=EnrollmentStatus.not_started,
        baseline_snapshot=baseline_for(db, employee),
    )
    db.add(enr)
    db.flush()
    return enr


def _assert_member_editable(enr: Enrollment) -> None:
    """Members can't edit past finalization — a broker reopen is required
    (the projected overrides are live coverage at that point)."""
    if enr.status in _FINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your enrollment has been finalized. Contact your broker or HR to reopen it.",
        )


def _reopen_if_submitted(enr: Enrollment) -> None:
    """Editing after submit returns the enrollment to in_progress — the member
    must resubmit, so window close can never auto-confirm elections that were
    changed after the submit-time validation ran."""
    if enr.status == EnrollmentStatus.submitted:
        enr.status = EnrollmentStatus.in_progress
        enr.submitted_at = None


def _require_open_enrollment(
    db: Session, member: CurrentMember
) -> tuple[Employee, EnrollmentWindow, Enrollment]:
    employee = resolve_member_employee(db, member)
    window = open_window_for(db, employee)
    if window is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No enrolment period is currently open."
        )
    return employee, window, _get_or_create_enrollment(db, window, employee)


@router.get("", response_model=PortalEnrollmentOut)
def my_enrollment(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> PortalEnrollmentOut:
    """The member's enrollment surface: window + own session + electable
    options. Everything None when no window is open (the page renders an
    informational empty state, not an error)."""
    employee = resolve_member_employee(db, member)
    window = open_window_for(db, employee)
    if window is None:
        return PortalEnrollmentOut()
    enr = _get_or_create_enrollment(db, window, employee)
    db.commit()
    return build_portal_enrollment(db, employee, enrollment=enr)


@router.put("/elections", response_model=EnrollmentOut)
@limiter.limit("30/minute")
def set_my_elections(
    request: Request,
    body: ElectionsUpdate,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    employee, _window, enr = _require_open_enrollment(db, member)
    _assert_member_editable(enr)
    _reopen_if_submitted(enr)
    apply_elections(db, enr, body.elections)
    write_member_audit(
        db, member, action="update_enrollment_elections", entity_type="enrollment",
        entity_id=enr.id, after={"count": len(body.elections)},
        employee_id=employee.id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.put("/leave", response_model=EnrollmentOut)
@limiter.limit("30/minute")
def set_my_leave(
    request: Request,
    body: LeaveElectionIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    employee, _window, enr = _require_open_enrollment(db, member)
    _assert_member_editable(enr)
    _reopen_if_submitted(enr)
    leave = apply_leave(db, enr, body)
    write_member_audit(
        db, member, action="update_enrollment_leave", entity_type="enrollment",
        entity_id=enr.id,
        after={"action": body.action, "days": body.days, "flex_amount": leave.flex_amount},
        employee_id=employee.id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)


@router.post("/submit", response_model=EnrollmentOut)
@limiter.limit("30/minute")
def submit_my_enrollment(
    request: Request,
    body: EnrollmentSubmitIn | None = None,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> EnrollmentOut:
    """Member submits their elections for broker confirmation. Projection into
    live coverage happens at broker confirm (or window-close deeming) — never
    directly from the portal."""
    employee, _window, enr = _require_open_enrollment(db, member)
    perform_submit(
        db, enr,
        acknowledge=bool(body and body.acknowledge_unpriced),
        actor_id=member.member_account_id,
    )
    write_member_audit(
        db, member, action="submit_enrollment", entity_type="enrollment",
        entity_id=enr.id, employee_id=employee.id,
    )
    db.commit()
    db.refresh(enr)
    return enrollment_detail(db, enr)
