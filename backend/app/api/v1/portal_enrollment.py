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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.downloads import attachment_header
from app.core.portal_auth import (
    CurrentMember,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.core.request_context import client_ip, user_agent
from app.core.storage import get_storage
from app.db.session import get_db
from app.models import Employee, Enrollment, EnrollmentWindow, StoredDocument
from app.models.enrollment import EnrollmentStatus
from app.models.stored_document import DOC_ENTITY_FORM_RESOURCE, STORAGE_AVAILABLE
from app.schemas.enrollment import (
    ElectionsUpdate,
    EnrollmentOut,
    EnrollmentSubmitIn,
    LeaveElectionIn,
    PortalEnrollmentOut,
)
from app.schemas.enrollment_forms import (
    FormSignIn,
    FormSubmissionSummary,
    MemberFormContextOut,
)
from app.services.enrollment_elections import (
    apply_elections,
    apply_leave,
    build_portal_enrollment,
    enrollment_detail,
    find_enrollment,
    lock_enrollment,
    member_window_for,
)
from app.services.enrollment_forms.config import resolve_settings
from app.services.enrollment_forms.context import build_form_context, submission_summary
from app.services.enrollment_forms.submission import SignatureMeta, sign_and_submit
from app.services.enrollment_lifecycle import baseline_for
from app.services.member_access import Capability

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
    try:
        with db.begin_nested():
            db.add(enr)
            db.flush()
        return enr
    except IntegrityError:
        # A concurrent first portal read/write can create the same lazy row.
        # The unique (window, employee) key chooses the winner; the savepoint
        # keeps the outer request transaction usable for the follow-up query.
        winner = find_enrollment(db, window, employee)
        if winner is None:
            raise
        return winner


def _assert_member_editable(enr: Enrollment) -> None:
    """Members can't edit past finalization — a broker reopen is required
    (the projected overrides are live coverage at that point)."""
    if enr.status in _FINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Your enrollment has been finalized. Contact your broker or HR to reopen it.",
        )


def _require_open_enrollment(
    db: Session, member: CurrentMember
) -> tuple[Employee, EnrollmentWindow, Enrollment]:
    employee = resolve_member_employee(db, member, requires=Capability.ELECT)
    window = member_window_for(db, employee)
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
    employee = resolve_member_employee(db, member, requires=Capability.ELECT)
    window = member_window_for(db, employee)
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
    enr = lock_enrollment(db, enr)
    _assert_member_editable(enr)
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
    enr = lock_enrollment(db, enr)
    _assert_member_editable(enr)
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
    """Retired for members: an enrolment is sent by SIGNING the e-form
    (``POST /portal/enrollment/sign``), which records the declarations, the
    signature and the PDF the broker and HR rely on. Left in place so an older
    cached portal bundle gets a clear, coded refusal instead of a 404 — and so
    nothing can reach "submitted" without a signed form on file."""
    _require_open_enrollment(db, member)
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "signature_required",
            "message": "Please refresh the page, then sign the enrolment form to send it.",
        },
    )


# -- Online enrolment e-form --------------------------------------------------


@router.get("/form", response_model=MemberFormContextOut)
def my_enrollment_form(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MemberFormContextOut:
    """The e-form around the election deck: particulars, family members with
    eligibility, the member's premium share, documents and declarations."""
    employee, window, enr = _require_open_enrollment(db, member)
    context = build_form_context(db, employee, window, enr)
    db.commit()
    return context


@router.post("/sign", response_model=FormSubmissionSummary)
@limiter.limit("10/minute")
def sign_my_enrollment_form(
    request: Request,
    body: FormSignIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> FormSubmissionSummary:
    """Sign and submit the enrolment form: applies the reviewed choices,
    submits the enrolment for broker confirmation, and files a signed,
    versioned record with its PDF."""
    employee, window, enr = _require_open_enrollment(db, member)
    enr = lock_enrollment(db, enr)
    _assert_member_editable(enr)
    submission = sign_and_submit(
        db,
        employee=employee,
        window=window,
        enrollment=enr,
        body=body,
        meta=SignatureMeta(
            ip=client_ip(request),
            user_agent=(user_agent(request) or "")[:512] or None,
            member_account_id=member.member_account_id,
        ),
    )
    write_member_audit(
        db, member, action="enrollment_form.signed", entity_type="enrollment_form",
        entity_id=submission.id,
        after={"reference_no": submission.reference_no, "version": submission.version},
        employee_id=employee.id,
        request=request,
    )
    db.commit()
    return submission_summary(submission, enr.status)


@router.get("/form/documents/{document_id}")
def download_form_document(
    document_id: str,
    request: Request,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Response:
    """A document the open period's form asks members to read (uploaded by the
    broker). Only documents listed on THIS window's form are served."""
    employee, window, _enr = _require_open_enrollment(db, member)
    settings, _row = resolve_settings(db, window)
    listed = {d.document_id for d in settings.documents if d.document_id}
    doc = db.get(StoredDocument, document_id)
    if (
        document_id not in listed
        or doc is None
        or doc.client_id != employee.client_id
        or doc.entity_type != DOC_ENTITY_FORM_RESOURCE
        or doc.entity_id != window.id
        or doc.storage_state != STORAGE_AVAILABLE
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = get_storage().read(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from None
    write_member_audit(
        db, member, action="enrollment_form.document_download",
        entity_type="stored_document", entity_id=doc.id, employee_id=employee.id,
        request=request,
    )
    db.commit()
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )
