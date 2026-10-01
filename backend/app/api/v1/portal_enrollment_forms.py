"""A member's own signed enrolment forms — list and PDF download.

Scoped to the member's Employee row via ``resolve_member_employee``; a form id
from the request is only ever served when it belongs to that employee.
Readable by leavers still inside run-off (``Capability.RECORD``) — a signed
form is part of their record.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.downloads import attachment_header
from app.core.portal_auth import CurrentMember, get_current_member, resolve_member_employee
from app.db.session import get_db
from app.models import Enrollment
from app.models.enrollment_form import FORM_SOURCE_PORTAL, EnrollmentFormSubmission
from app.schemas.enrollment_forms import FormSubmissionSummary
from app.services.enrollment_forms.context import submission_summary
from app.services.enrollment_forms.register import submission_pdf
from app.services.member_access import Capability

router = APIRouter(
    prefix="/portal/enrollment-forms",
    tags=["portal-enrollment-forms"],
    dependencies=[Depends(get_current_member)],
)


@router.get("", response_model=list[FormSubmissionSummary])
def my_forms(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[FormSubmissionSummary]:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    rows = db.execute(
        select(EnrollmentFormSubmission, Enrollment.status)
        .outerjoin(Enrollment, Enrollment.id == EnrollmentFormSubmission.enrollment_id)
        .where(
            EnrollmentFormSubmission.employee_id == employee.id,
            # Paper scans are the broker's record of a paper form; the member
            # sees the forms they signed here.
            EnrollmentFormSubmission.source == FORM_SOURCE_PORTAL,
        )
        .order_by(EnrollmentFormSubmission.created_at.desc())
    ).all()
    return [submission_summary(sub, enr_status) for sub, enr_status in rows]


@router.get("/{submission_id}/pdf")
def my_form_pdf(
    submission_id: str,
    request: Request,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Response:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    sub = db.get(EnrollmentFormSubmission, submission_id)
    if (
        sub is None
        or sub.employee_id != employee.id
        or sub.client_id != employee.client_id
        or sub.source != FORM_SOURCE_PORTAL
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    found = submission_pdf(db, sub)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form not found")
    content, doc = found
    write_member_audit(
        db, member, action="enrollment_form.download", entity_type="enrollment_form",
        entity_id=sub.id, employee_id=employee.id, request=request,
    )
    db.commit()
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )
