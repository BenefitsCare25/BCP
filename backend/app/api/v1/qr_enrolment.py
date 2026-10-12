"""Broker controls for no-login QR enrolment.

Shared employee QR (per enrolment period):
- GET   /enrollment-windows/{id}/qr-entry          status and counts
- POST  /enrollment-windows/{id}/qr-entry          turn on (creates the QR once)
- PATCH /enrollment-windows/{id}/qr-entry          pause or resume
- POST  /enrollment-windows/{id}/qr-entry/rotate   retire every copy, new QR
- POST  /enrollment-windows/{id}/qr-entry/link     the link to show as a QR (audited)

QR submissions:
- GET  /policy-years/{id}/qr-intakes               register
- GET  /qr-intakes/{id}                            detail + roster suggestions
- GET  /qr-intakes/{id}/candidates?q=              roster search
- GET  /qr-intakes/{id}/pdf
- POST /qr-intakes/{id}/link | /reject | /restore

HR download batches (bulk-export roles only):
- GET  /policy-years/{id}/hr-batches               list
- GET  /policy-years/{id}/hr-batches/preview       what a selection would contain
- POST /policy-years/{id}/hr-batches               prepare the ZIP
- POST /hr-batches/{id}/grants                     new link + PIN (revokes the last)
- POST /hr-grants/{id}/revoke
- POST /hr-batches/{id}/close

Registered inside the broker ``require_write_access`` loop: broker viewers read
only, and a read-only platform grant cannot write.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_access_audit, write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import load_enrollment_window, load_policy_year, require_broker_admin
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.core.storage import company_firm_id
from app.db.session import get_db
from app.models import (
    EnrollmentWindow,
    HrDownloadBatch,
    HrDownloadGrant,
    PolicyYear,
    PublicFormEntry,
)
from app.models.qr_enrolment import GRANT_REVOKE_MANUAL
from app.schemas.enrollment_forms import FormSubmissionSummary
from app.schemas.qr_enrolment import (
    BatchFilterIn,
    BatchListOut,
    BatchOut,
    BatchPreviewOut,
    FormBatchStatus,
    GrantIssuedOut,
    GrantIssueIn,
    GrantOut,
    IntakeCandidateOut,
    IntakeDetailOut,
    IntakeLinkIn,
    IntakeListOut,
    IntakeRejectIn,
    QrEntryLinkOut,
    QrEntryOut,
    QrEntryUpdateIn,
)
from app.services.enrollment_forms.context import submission_summary
from app.services.qr_enrolment import batches as batch_svc
from app.services.qr_enrolment import review
from app.services.qr_enrolment.access import HR_DOWNLOAD_PATH, link_url
from app.services.qr_enrolment.entries import (
    create_entry,
    entry_out,
    entry_url,
    find_entry,
    rotate_entry,
)
from app.services.qr_enrolment.intake import intake_pdf

router = APIRouter(tags=["qr-enrolment"])

_STATUS_Q = Query(default=None, alias="status", pattern="^(pending|linked|rejected)$")


def _entry_or_404(db: Session, window: EnrollmentWindow) -> PublicFormEntry:
    entry = find_entry(db, window.id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The QR form is not turned on.")
    return entry


@router.get("/enrollment-windows/{window_id}/qr-entry", response_model=QrEntryOut)
def get_qr_entry(
    window: EnrollmentWindow = Depends(load_enrollment_window),
    db: Session = Depends(get_db),
) -> QrEntryOut:
    return entry_out(db, window, find_entry(db, window.id))


@router.post("/enrollment-windows/{window_id}/qr-entry", response_model=QrEntryOut)
def enable_qr_entry(
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QrEntryOut:
    if window.status == "closed":
        raise HTTPException(status.HTTP_409_CONFLICT, "This enrolment period has closed.")
    existed = find_entry(db, window.id) is not None
    entry = create_entry(db, window, user.user_id)
    write_audit(
        db, user, "qr_entry.enabled" if existed else "qr_entry.created", "public_form_entry",
        entry.id, after={"window_id": window.id}, client_id=window.client_id,
    )
    db.commit()
    return entry_out(db, window, entry)


@router.patch("/enrollment-windows/{window_id}/qr-entry", response_model=QrEntryOut)
def update_qr_entry(
    body: QrEntryUpdateIn,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QrEntryOut:
    entry = _entry_or_404(db, window)
    entry.enabled = body.enabled
    write_audit(
        db, user, "qr_entry.resumed" if body.enabled else "qr_entry.paused",
        "public_form_entry", entry.id, after={"window_id": window.id},
        client_id=window.client_id,
    )
    db.commit()
    return entry_out(db, window, entry)


@router.post("/enrollment-windows/{window_id}/qr-entry/rotate", response_model=QrEntryOut)
def rotate_qr_entry(
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QrEntryOut:
    entry = _entry_or_404(db, window)
    rotate_entry(entry)
    write_audit(
        db, user, "qr_entry.rotated", "public_form_entry", entry.id,
        after={"window_id": window.id}, client_id=window.client_id,
    )
    db.commit()
    return entry_out(db, window, entry)


@router.post("/enrollment-windows/{window_id}/qr-entry/link", response_model=QrEntryLinkOut)
def reveal_qr_link(
    request: Request,
    window: EnrollmentWindow = Depends(load_enrollment_window),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QrEntryLinkOut:
    entry = _entry_or_404(db, window)
    url = entry_url(db, entry, company_firm_id(db, window.client_id))
    write_access_audit(
        db, user, request, "qr_entry.link_viewed", "public_form_entry", entry.id,
        client_id=window.client_id,
    )
    db.commit()
    return QrEntryLinkOut(url=url)


# ── QR submissions ───────────────────────────────────────────────────────────


@router.get("/policy-years/{policy_year_id}/qr-intakes", response_model=IntakeListOut)
def list_qr_intakes(
    window_id: str | None = Query(default=None, max_length=36),
    status_: str | None = _STATUS_Q,
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> IntakeListOut:
    return review.list_intakes(
        db, client_id=year.client_id, policy_year_id=year.id, window_id=window_id,
        status_=status_, query=q, offset=offset, limit=limit,
    )


@router.get("/qr-intakes/{intake_id}", response_model=IntakeDetailOut)
def get_qr_intake(
    intake_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> IntakeDetailOut:
    intake = review.find_intake(db, user.client_id or "", intake_id)
    out = review.detail(db, intake)
    write_access_audit(
        db, user, request, "qr_intake.viewed", "public_intake", intake.id,
        client_id=intake.client_id,
    )
    db.commit()
    return out


@router.get("/qr-intakes/{intake_id}/candidates", response_model=list[IntakeCandidateOut])
def search_qr_candidates(
    intake_id: str,
    q: str | None = Query(default=None, max_length=100),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntakeCandidateOut]:
    intake = review.find_intake(db, user.client_id or "", intake_id)
    return review.candidates(db, intake, q)


@router.get("/qr-intakes/{intake_id}/pdf")
def download_qr_intake_pdf(
    intake_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    intake = review.find_intake(db, user.client_id or "", intake_id)
    found = intake_pdf(db, intake)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The form file is not available")
    content, doc = found
    write_access_audit(
        db, user, request, "qr_intake.download", "public_intake", intake.id,
        client_id=intake.client_id,
    )
    db.commit()
    return Response(
        content=content, media_type="application/pdf",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )


@router.post("/qr-intakes/{intake_id}/link", response_model=FormSubmissionSummary)
def link_qr_intake(
    intake_id: str,
    body: IntakeLinkIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FormSubmissionSummary:
    intake = review.find_intake(db, user.client_id or "", intake_id, lock=True)
    sub = review.link_intake(
        db, intake, body.employee_id, user.user_id, body.note,
        confirm_supersede=body.confirm_supersede,
    )
    write_audit(
        db, user, "qr_intake.linked", "public_intake", intake.id,
        after={"reference_no": intake.reference_no, "submission_id": sub.id},
        employee_id=sub.employee_id, client_id=intake.client_id,
    )
    db.commit()
    return submission_summary(sub)


@router.post("/qr-intakes/{intake_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_qr_intake(
    intake_id: str,
    body: IntakeRejectIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    intake = review.find_intake(db, user.client_id or "", intake_id, lock=True)
    review.reject_intake(intake, body.reason, body.note, user.user_id)
    write_audit(
        db, user, "qr_intake.rejected", "public_intake", intake.id,
        after={"reference_no": intake.reference_no, "reason": body.reason},
        client_id=intake.client_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/qr-intakes/{intake_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_qr_intake(
    intake_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    intake = review.find_intake(db, user.client_id or "", intake_id, lock=True)
    review.restore_intake(intake)
    write_audit(
        db, user, "qr_intake.restored", "public_intake", intake.id,
        after={"reference_no": intake.reference_no}, client_id=intake.client_id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── HR download batches ──────────────────────────────────────────────────────


def _batch(db: Session, user: CurrentUser, batch_id: str) -> HrDownloadBatch:
    batch = db.get(HrDownloadBatch, batch_id)
    if batch is None or batch.client_id != (user.client_id or ""):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download batch not found")
    return batch


@router.get("/policy-years/{policy_year_id}/hr-batches", response_model=BatchListOut)
def list_hr_batches(
    year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> BatchListOut:
    rows = list(db.scalars(
        select(HrDownloadBatch)
        .where(
            HrDownloadBatch.client_id == year.client_id,
            HrDownloadBatch.policy_year_id == year.id,
        )
        .order_by(HrDownloadBatch.created_at.desc()).limit(50)
    ))
    for batch in rows:
        batch_svc.retire_if_due(db, batch)
    db.commit()
    return BatchListOut(items=[batch_svc.batch_out(db, b) for b in rows])


@router.get("/policy-years/{policy_year_id}/hr-batches/preview", response_model=BatchPreviewOut)
def preview_hr_batch(
    window_id: str | None = Query(default=None, max_length=36),
    statuses: list[FormBatchStatus] = Query(default=["submitted", "acknowledged"]),
    include_pending_qr: bool = False,
    year: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> BatchPreviewOut:
    f = BatchFilterIn.model_validate({
        "window_id": window_id, "statuses": statuses, "include_pending_qr": include_pending_qr,
    })
    items, total = batch_svc.preview(db, year.client_id, year.id, f)
    return BatchPreviewOut(items=items, total=total, limit=batch_svc.MAX_BATCH_FILES)


@router.post(
    "/policy-years/{policy_year_id}/hr-batches",
    response_model=BatchOut, status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
def prepare_hr_batch(
    request: Request,
    body: BatchFilterIn,
    year: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> BatchOut:
    window = db.get(EnrollmentWindow, body.window_id) if body.window_id else None
    if body.window_id and (window is None or window.policy_year_id != year.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrolment period not found")
    stamp = datetime.now(UTC).astimezone().strftime("%d %b %Y")
    label = f"{window.name if window else f'Benefit year {year.year}'} · {stamp}"
    batch = batch_svc.prepare_batch(
        db, client_id=year.client_id, policy_year_id=year.id, f=body, label=label[:160],
        user_id=user.user_id,
    )
    write_audit(
        db, user, "hr_batch.prepared", "hr_download_batch", batch.id,
        after={"files": batch.file_count, "bytes": batch.size_bytes,
               "include_pending_qr": body.include_pending_qr},
        request=request, client_id=year.client_id,
    )
    db.commit()
    return batch_svc.batch_out(db, batch)


@router.post("/hr-batches/{batch_id}/grants", response_model=GrantIssuedOut)
@limiter.limit("20/minute")
def issue_hr_grant(
    batch_id: str,
    request: Request,
    body: GrantIssueIn,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> GrantIssuedOut:
    batch = _batch(db, user, batch_id)
    grant, token, pin = batch_svc.issue_grant(db, batch, user.user_id, body.recipient_note)
    url = link_url(db, company_firm_id(db, batch.client_id), HR_DOWNLOAD_PATH, token)
    write_audit(
        db, user, "hr_grant.issued", "hr_download_grant", grant.id,
        after={"batch_id": batch.id, "recipient_note": grant.recipient_note},
        request=request, client_id=batch.client_id,
    )
    db.commit()
    return GrantIssuedOut(
        grant=batch_svc.grant_out(grant, batch_svc.now_utc()), url=url, pin=pin
    )


@router.post("/hr-grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_hr_grant(
    grant_id: str,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> GrantOut:
    grant = db.get(HrDownloadGrant, grant_id)
    if grant is None or grant.client_id != (user.client_id or ""):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Download link not found")
    batch_svc.revoke_grant(grant, user.user_id, GRANT_REVOKE_MANUAL)
    write_audit(
        db, user, "hr_grant.revoked", "hr_download_grant", grant.id,
        after={"batch_id": grant.batch_id}, client_id=grant.client_id,
    )
    db.commit()
    return batch_svc.grant_out(grant, batch_svc.now_utc())


@router.post("/hr-batches/{batch_id}/close", response_model=BatchOut)
def close_hr_batch(
    batch_id: str,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> BatchOut:
    batch = _batch(db, user, batch_id)
    batch_svc.close_batch(db, batch, user.user_id)
    write_audit(
        db, user, "hr_batch.closed", "hr_download_batch", batch.id,
        after={"files": batch.file_count}, client_id=batch.client_id,
    )
    db.commit()
    return batch_svc.batch_out(db, batch)
