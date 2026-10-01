"""Broker surface for online enrolment e-forms.

- GET  /enrollment-windows/{id}/form-config              setup (generated until saved)
- PUT  /enrollment-windows/{id}/form-config              save setup
- POST /enrollment-windows/{id}/form-config/documents    upload a document to read
- GET  /enrollment-windows/{id}/form-config/documents/{doc_id}
- GET  /policy-years/{id}/enrollment-forms               register (filters + paging)
- GET  /policy-years/{id}/enrollment-forms/export.zip    PDFs
- GET  /policy-years/{id}/enrollment-forms/export.xlsx   summary + family sheet
- POST /policy-years/{id}/enrollment-forms/paper         file a scanned paper form
- GET  /enrollment-forms/{id}/pdf
- POST /enrollment-forms/{id}/acknowledge

Tenant scoping rides on ``load_enrollment_window`` / ``load_policy_year`` and,
for a submission id, on the caller's client. Registered inside the broker
``require_write_access`` loop, so broker viewers stay read-only.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.audit import write_access_audit, write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_enrollment_window, load_policy_year
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.core.storage import get_storage
from app.db.session import get_db
from app.models import Client, Employee, EnrollmentWindow, PolicyYear, StoredDocument
from app.models.enrollment_form import (
    FORM_STATUS_ACKNOWLEDGED,
    FORM_STATUS_SUBMITTED,
    EnrollmentFormSubmission,
)
from app.models.stored_document import DOC_ENTITY_FORM_RESOURCE, STORAGE_AVAILABLE
from app.schemas.enrollment_forms import (
    FormAcknowledgeIn,
    FormConfigOut,
    FormRegisterOut,
    FormSettings,
    FormSubmissionSummary,
)
from app.services.claims import attach_document
from app.services.enrollment_forms.config import config_out, save_settings
from app.services.enrollment_forms.context import submission_summary
from app.services.enrollment_forms.register import (
    RegisterFilter,
    build_workbook,
    build_zip,
    find_submission,
    list_register,
    submission_pdf,
    zip_response,
)
from app.services.enrollment_forms.submission import record_paper_form

router = APIRouter(tags=["enrollment-forms"])

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/enrollment-windows/{window_id}/form-config", response_model=FormConfigOut)
def get_form_config(
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> FormConfigOut:
    return config_out(db, window)


@router.put("/enrollment-windows/{window_id}/form-config", response_model=FormConfigOut)
def put_form_config(
    body: FormSettings,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FormConfigOut:
    row = save_settings(db, window, body, user.user_id)
    write_audit(
        db, user, "enrollment_form.config_saved", "enrollment_form_config", row.id,
        after={"window_id": window.id, "clauses": len(body.clauses),
               "documents": len(body.documents), "contributions": len(body.contributions)},
    )
    db.commit()
    return config_out(db, window)


@router.post("/enrollment-windows/{window_id}/form-config/documents")
@limiter.limit("20/minute")
async def upload_form_document(
    request: Request,
    file: UploadFile = File(...),
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Store a document members must read (MAS guide, product summary, benefit
    schedule). The caller then lists it in the form setup by ``document_id``."""
    client = db.get(Client, window.client_id)
    assert client is not None
    doc = await attach_document(
        db,
        client_id=client.id,
        broker_firm_id=client.broker_firm_id,
        entity_type=DOC_ENTITY_FORM_RESOURCE,
        entity_id=window.id,
        file=file,
        uploaded_by_user_id=user.user_id,
    )
    write_audit(
        db, user, "enrollment_form.document_uploaded", "stored_document", doc.id,
        after={"window_id": window.id, "file_name": doc.file_name},
    )
    db.commit()
    return {"document_id": doc.id, "file_name": doc.file_name}


@router.get("/enrollment-windows/{window_id}/form-config/documents/{document_id}")
def download_form_document(
    document_id: str,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> Response:
    doc = db.get(StoredDocument, document_id)
    if (
        doc is None
        or doc.client_id != window.client_id
        or doc.entity_type != DOC_ENTITY_FORM_RESOURCE
        or doc.entity_id != window.id
        or doc.storage_state != STORAGE_AVAILABLE
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = get_storage().read(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from None
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )


def _filter(
    year: PolicyYear,
    window_id: str | None,
    status_: str | None,
    source: str | None,
    q: str | None,
    include_superseded: bool,
) -> RegisterFilter:
    return RegisterFilter(
        client_id=year.client_id,
        policy_year_id=year.id,
        window_id=window_id,
        status=status_,
        source=source,
        query=q,
        include_superseded=include_superseded,
    )


_STATUS_Q = Query(default=None, alias="status", pattern="^(submitted|acknowledged|superseded)$")
_SOURCE_Q = Query(default=None, pattern="^(portal|paper)$")
_TEXT_Q = Query(default=None, max_length=100)


@router.get("/policy-years/{policy_year_id}/enrollment-forms", response_model=FormRegisterOut)
def list_forms(
    window_id: str | None = Query(default=None, max_length=36),
    status_: str | None = _STATUS_Q,
    source: str | None = _SOURCE_Q,
    q: str | None = _TEXT_Q,
    include_superseded: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> FormRegisterOut:
    f = _filter(year, window_id, status_, source, q, include_superseded)
    return list_register(db, f, offset, limit)


@router.get("/policy-years/{policy_year_id}/enrollment-forms/export.zip")
def export_forms_zip(
    request: Request,
    window_id: str | None = Query(default=None, max_length=36),
    status_: str | None = _STATUS_Q,
    source: str | None = _SOURCE_Q,
    q: str | None = _TEXT_Q,
    year: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    spool, written = build_zip(db, _filter(year, window_id, status_, source, q, False))
    if not written:
        spool.close()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No forms match these filters.")
    write_access_audit(db, user, request, "enrollment_form.export_zip", "policy_year", year.id)
    db.commit()
    return zip_response(spool, f"Enrolment forms {year.year}.zip")


@router.get("/policy-years/{policy_year_id}/enrollment-forms/export.xlsx")
def export_forms_xlsx(
    request: Request,
    window_id: str | None = Query(default=None, max_length=36),
    status_: str | None = _STATUS_Q,
    source: str | None = _SOURCE_Q,
    q: str | None = _TEXT_Q,
    year: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    content = build_workbook(db, _filter(year, window_id, status_, source, q, False))
    write_access_audit(db, user, request, "enrollment_form.export_xlsx", "policy_year", year.id)
    db.commit()
    return Response(
        content=content,
        media_type=_XLSX,
        headers={"Content-Disposition": attachment_header(f"Enrolment forms {year.year}.xlsx")},
    )


@router.post(
    "/policy-years/{policy_year_id}/enrollment-forms/paper",
    response_model=FormSubmissionSummary,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")
async def file_paper_form(
    request: Request,
    employee_id: str = Form(..., max_length=36),
    window_id: str | None = Form(default=None, max_length=36),
    note: str | None = Form(default=None, max_length=2000),
    file: UploadFile = File(...),
    year: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FormSubmissionSummary:
    """File a scanned paper enrolment form for a member without portal access."""
    employee = db.get(Employee, employee_id)
    if employee is None or employee.policy_year_id != year.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found in this benefit year")
    window = None
    if window_id:
        window = db.get(EnrollmentWindow, window_id)
        if window is None or window.policy_year_id != year.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found")
    sub = await record_paper_form(
        db, employee=employee, window=window, file=file, user_id=user.user_id,
        note=(note or "").strip() or None,
    )
    write_audit(
        db, user, "enrollment_form.paper_filed", "enrollment_form", sub.id,
        after={"reference_no": sub.reference_no}, employee_id=employee.id, request=request,
    )
    db.commit()
    return submission_summary(sub)


def _load_submission(
    db: Session, user: CurrentUser, submission_id: str
) -> EnrollmentFormSubmission:
    sub = find_submission(db, user.client_id or "", submission_id)
    if sub is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment form not found")
    return sub


@router.get("/enrollment-forms/{submission_id}/pdf")
def download_form_pdf(
    submission_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    sub = _load_submission(db, user, submission_id)
    found = submission_pdf(db, sub)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The form file is not available")
    content, doc = found
    write_access_audit(
        db, user, request, "enrollment_form.download", "enrollment_form", sub.id,
        employee_id=sub.employee_id,
    )
    db.commit()
    return Response(
        content=content,
        media_type=doc.mime_type or "application/pdf",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )


@router.post("/enrollment-forms/{submission_id}/acknowledge", response_model=FormSubmissionSummary)
def acknowledge_form(
    submission_id: str,
    body: FormAcknowledgeIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FormSubmissionSummary:
    """Mark a form received and checked. Confirming the ELECTIONS into live
    coverage stays on the enrolment Members tab (its existing confirm)."""
    sub = _load_submission(db, user, submission_id)
    if sub.status != FORM_STATUS_SUBMITTED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only the latest submitted version can be acknowledged.",
        )
    sub.status = FORM_STATUS_ACKNOWLEDGED
    sub.acknowledged_at = datetime.now(UTC)
    sub.acknowledged_by = user.user_id
    if body.note:
        sub.broker_note = body.note.strip()
    write_audit(
        db, user, "enrollment_form.acknowledged", "enrollment_form", sub.id,
        after={"reference_no": sub.reference_no}, employee_id=sub.employee_id,
    )
    db.commit()
    return submission_summary(sub)
