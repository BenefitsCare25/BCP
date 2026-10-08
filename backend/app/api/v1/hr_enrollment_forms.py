"""Company HR view of online enrolment e-forms — read and download only.

HR sees every form filed for its own company (online and scanned paper) and
can download a single PDF, all PDFs as a ZIP, or the Excel summary. Reviewing
and acknowledging stays with the broker (user decision). Every download is
written to the access trail.

The Excel summary always masks NRIC/FIN for HR. The bulk ZIP holds every
member's signed form unredacted, so only an HR administrator may pull it; a
single signed form stays the original legal document for either HR role.
Both bulk exports are rate limited and audited with their row counts.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.hr_claims import delegated_hr
from app.core.audit import write_access_audit, write_audit
from app.core.auth import CurrentUser
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import EnrollmentWindow, PolicyYear
from app.schemas.enrollment_forms import FormRegisterOut
from app.services.enrollment_forms.register import (
    RegisterFilter,
    build_workbook,
    build_zip,
    find_submission,
    list_register,
    submission_pdf,
    zip_response,
)

router = APIRouter(prefix="/hr/enrollment-forms", tags=["hr-enrollment-forms"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_BULK_EXPORT_LIMIT = "5/minute"
_HR_ADMIN = "client_admin"


class HrFormWindow(BaseModel):
    id: str
    name: str
    policy_year: int
    opens_at: datetime
    closes_at: datetime
    status: str


def _filter(
    user: CurrentUser, window_id: str | None, status_: str | None, q: str | None
) -> RegisterFilter:
    return RegisterFilter(
        client_id=user.client_id or "", window_id=window_id, status=status_, query=q
    )


_STATUS_Q = Query(
    default=None, alias="status", pattern="^(submitted|acknowledged|returned|cancelled)$"
)
_TEXT_Q = Query(default=None, max_length=100)
_WINDOW_Q = Query(default=None, max_length=36)


@router.get("/windows", response_model=list[HrFormWindow])
def list_windows(
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> list[HrFormWindow]:
    rows = db.execute(
        select(EnrollmentWindow, PolicyYear.year)
        .join(PolicyYear, PolicyYear.id == EnrollmentWindow.policy_year_id)
        .where(
            EnrollmentWindow.client_id == user.client_id,
            EnrollmentWindow.status != "draft",
        )
        .order_by(EnrollmentWindow.opens_at.desc())
    ).all()
    return [
        HrFormWindow(
            id=w.id,
            name=w.name,
            policy_year=year,
            opens_at=w.opens_at,
            closes_at=w.closes_at,
            status=w.status,
        )
        for w, year in rows
    ]


@router.get("", response_model=FormRegisterOut)
def list_forms(
    window_id: str | None = _WINDOW_Q,
    status_: str | None = _STATUS_Q,
    q: str | None = _TEXT_Q,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> FormRegisterOut:
    return list_register(db, _filter(user, window_id, status_, q), offset, limit)


@router.get("/export.zip")
@limiter.limit(_BULK_EXPORT_LIMIT)
def export_zip(
    request: Request,
    window_id: str | None = _WINDOW_Q,
    status_: str | None = _STATUS_Q,
    q: str | None = _TEXT_Q,
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> Response:
    if user.role != _HR_ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only an HR administrator can download every signed form at once.",
        )
    spool, written = build_zip(db, _filter(user, window_id, status_, q))
    if not written:
        spool.close()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No forms match these filters.")
    write_audit(
        db, user, "enrollment_form.export_zip", "client", user.client_id or "",
        after={"forms": written}, request=request,
    )
    db.commit()
    return zip_response(spool, "Enrolment forms.zip")


@router.get("/export.xlsx")
@limiter.limit(_BULK_EXPORT_LIMIT)
def export_xlsx(
    request: Request,
    window_id: str | None = _WINDOW_Q,
    status_: str | None = _STATUS_Q,
    q: str | None = _TEXT_Q,
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> Response:
    workbook = build_workbook(db, _filter(user, window_id, status_, q), masked=True)
    write_audit(
        db, user, "enrollment_form.export_xlsx", "client", user.client_id or "",
        after={
            "masked": True,
            "forms": workbook.forms,
            "family_members": workbook.family_members,
        },
        request=request,
    )
    db.commit()
    return Response(
        content=workbook.content,
        media_type=_XLSX,
        headers={"Content-Disposition": attachment_header("Enrolment forms.xlsx")},
    )


@router.get("/{submission_id}/pdf")
def download_pdf(
    submission_id: str,
    request: Request,
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> Response:
    sub = find_submission(db, user.client_id or "", submission_id)
    found = submission_pdf(db, sub) if sub is not None else None
    if sub is None or found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment form not found")
    content, doc = found
    write_access_audit(
        db,
        user,
        request,
        "enrollment_form.download",
        "enrollment_form",
        sub.id,
        employee_id=sub.employee_id,
    )
    db.commit()
    return Response(
        content=content,
        media_type=doc.mime_type or "application/pdf",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )
