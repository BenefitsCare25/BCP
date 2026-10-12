"""No-login employee enrolment form, opened from the company's shared QR.

- GET  /public/enrol                       the form (or its closed/paused state)
- GET  /public/enrol/documents/{doc_id}    a document the form asks to read
- POST /public/enrol/submissions           submit; returns the receipt
- POST /public/enrol/copy                  the employee's own PDF, once

Unauthenticated: registered outside the broker gate. The host selects the
broker firm; the link token (``X-Inspro-Link`` header, never the URL path)
must then exist inside that firm. A submission is a pending intake — it changes
no employee, dependant, election or coverage.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.cookie_auth import require_same_origin
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.core.storage import StorageScopeError, company_firm_id, get_storage
from app.core.tenant_resolution import require_client_surface, require_live_host_firm
from app.db.session import get_db
from app.models import StoredDocument
from app.models.stored_document import DOC_ENTITY_FORM_RESOURCE, STORAGE_AVAILABLE
from app.schemas.qr_enrolment import (
    PublicCopyIn,
    PublicFormOut,
    PublicReceiptOut,
    PublicSubmissionIn,
)
from app.services.claims import assert_document_scope
from app.services.enrollment_forms.config import resolve_settings
from app.services.qr_enrolment.access import presented_token, route_host_firm
from app.services.qr_enrolment.catalog import cached_open_form, public_form
from app.services.qr_enrolment.intake import (
    form_status,
    redeem_copy,
    resolve_entry,
    submit_intake,
)

router = APIRouter(
    prefix="/public/enrol",
    tags=["public-enrolment"],
    dependencies=[Depends(require_client_surface), Depends(require_live_host_firm)],
)

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("", response_model=PublicFormOut)
# A whole office opens the QR at once from one address after HR's email.
@limiter.limit("300/minute")
def get_public_form(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> PublicFormOut:
    token = presented_token(request)
    firm_id = route_host_firm(request, db)
    entry, window = resolve_entry(db, token, firm_id)
    state = form_status(entry, window)
    response.headers.update(_NO_STORE)
    if state == "open":
        return cached_open_form(db, window)
    return public_form(db, window, state, None)


@router.get("/documents/{document_id}")
@limiter.limit("30/minute")
def get_public_document(
    document_id: str, request: Request, db: Session = Depends(get_db)
) -> Response:
    token = presented_token(request)
    firm_id = route_host_firm(request, db)
    entry, window = resolve_entry(db, token, firm_id)
    if form_status(entry, window) != "open":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    settings, _ = resolve_settings(db, window)
    # The page names a document by its entry in the form setup; only an entry
    # that carries an uploaded file resolves, and only to that file.
    stored = {d.id: d.document_id for d in settings.documents if d.document_id}
    stored_id = stored.get(document_id)
    doc = db.get(StoredDocument, stored_id) if stored_id else None
    if (
        doc is None
        or doc.client_id != window.client_id
        or doc.entity_type != DOC_ENTITY_FORM_RESOURCE
        or doc.entity_id != window.id
        or doc.storage_state != STORAGE_AVAILABLE
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        assert_document_scope(
            doc.storage_path, company_firm_id(db, window.client_id), window.client_id
        )
        content = get_storage().read(doc.storage_path)
    except (FileNotFoundError, StorageScopeError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found") from None
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": attachment_header(doc.file_name), **_NO_STORE},
    )


@router.post(
    "/submissions", response_model=PublicReceiptOut, status_code=status.HTTP_201_CREATED
)
@limiter.limit("120/minute")
def submit_public_form(
    body: PublicSubmissionIn, request: Request, response: Response,
    db: Session = Depends(get_db),
) -> PublicReceiptOut:
    require_same_origin(request)
    token = presented_token(request)
    firm_id = route_host_firm(request, db)
    entry, window = resolve_entry(db, token, firm_id)
    intake, copy_key = submit_intake(db, entry, window, body)
    receipt = PublicReceiptOut(
        reference_no=intake.reference_no,
        submitted_at=intake.created_at,
        copy_key=copy_key,
        copy_expires_at=intake.copy_expires_at,  # type: ignore[arg-type]
    )
    db.commit()
    response.headers.update(_NO_STORE)
    return receipt


@router.post("/copy")
@limiter.limit("20/minute")
def download_own_copy(
    body: PublicCopyIn, request: Request, db: Session = Depends(get_db)
) -> Response:
    require_same_origin(request)
    token = presented_token(request)
    firm_id = route_host_firm(request, db)
    entry, _window = resolve_entry(db, token, firm_id)
    content, file_name = redeem_copy(db, entry, body.reference_no, body.copy_key)
    db.commit()
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": attachment_header(file_name), **_NO_STORE},
    )
