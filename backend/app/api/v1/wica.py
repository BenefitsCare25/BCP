"""Broker-only WICA: incident -> documents -> pack -> manual outcome."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
)
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.clock import today
from app.core.deps import require_claim_access, require_claim_configuration, require_client_id
from app.core.downloads import attachment_header
from app.core.rate_limit import limiter
from app.core.storage import DOCUMENT_SUFFIXES, MAX_DOCUMENT_BYTES, document_path, get_storage
from app.core.uploads import saved_upload
from app.db.session import get_db
from app.models import (
    Employee,
    StoredDocument,
    WicaDocument,
    WicaIncident,
    WicaPack,
    WicaPeriod,
    WicaSettings,
)
from app.schemas.wica import ActionIn, IncidentIn, PackIn, RevisionIn, SentIn, SettingsIn, TagIn
from app.services import wica as svc
from app.services.claims import _sniff_mime
from app.services.file_security import scan_quarantined_document

router = APIRouter(prefix="/wica", tags=["wica"], dependencies=[Depends(require_claim_access)])
logger = logging.getLogger(__name__)


def audit(
    db: Session, user: CurrentUser, action: str, entity_id: str, after: dict[str, Any] | None = None
) -> None:
    write_audit(db, user, f"wica.{action}", "wica", entity_id, after=after)


@router.get("/settings")
def settings(
    user: CurrentUser = Depends(require_claim_access), db: Session = Depends(get_db)
) -> dict[str, Any]:
    client_id = require_client_id(user)
    row = db.get(WicaSettings, client_id)
    periods = db.scalars(
        select(WicaPeriod)
        .where(WicaPeriod.client_id == client_id)
        .order_by(WicaPeriod.start_date.desc())
    ).all()
    used = set(
        db.scalars(
            select(WicaIncident.period_id).where(WicaIncident.client_id == client_id).distinct()
        )
    )
    return {
        "enabled": row.enabled if row else False,
        "revision": row.revision if row else 0,
        "periods": [
            {
                **{
                    key: getattr(p, key)
                    for key in ("id", "label", "start_date", "end_date", "grace_days")
                },
                "in_use": p.id in used,
            }
            for p in periods
        ],
    }


@router.put("/settings")
def save_settings(
    body: SettingsIn,
    user: CurrentUser = Depends(require_claim_configuration),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    client_id = require_client_id(user)
    periods = sorted(body.periods, key=lambda p: p.start_date)
    if len({p.id for p in periods}) != len(periods):
        raise HTTPException(422, "Each period must have a unique ID.")
    if any(a.end_date >= b.start_date for a, b in pairwise(periods)):
        raise HTTPException(422, "WICA periods cannot overlap.")
    if body.enabled and not periods:
        raise HTTPException(422, "Add a WICA period before enabling WICA.")
    row = db.get(WicaSettings, client_id)
    if row is None:
        if body.revision != 0:
            raise HTTPException(409, "Settings have changed. Refresh and try again.")
        row = WicaSettings(client_id=client_id, revision=1, enabled=body.enabled)
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Settings have changed. Refresh and try again.") from None
    else:
        result = db.scalar(
            update(WicaSettings)
            .where(WicaSettings.client_id == client_id, WicaSettings.revision == body.revision)
            .values(enabled=body.enabled, revision=body.revision + 1)
            .returning(WicaSettings.client_id)
        )
        if result is None:
            raise HTTPException(409, "Settings have changed. Refresh and try again.")
    existing = {
        p.id: p for p in db.scalars(select(WicaPeriod).where(WicaPeriod.client_id == client_id))
    }
    # Existing periods are retained. Periods already referenced by incidents are immutable.
    if not set(existing).issubset({str(p.id) for p in periods}):
        raise HTTPException(422, "Existing periods must be retained.")
    for p in periods:
        data = p.model_dump(exclude={"id"})
        old = existing.get(str(p.id))
        if old:
            used = db.scalar(
                select(WicaIncident.id).where(WicaIncident.period_id == old.id).limit(1)
            )
            if used and any(getattr(old, k) != v for k, v in data.items()):
                raise HTTPException(409, "A period used by an incident cannot be changed.")
            for k, v in data.items():
                setattr(old, k, v)
        else:
            if db.get(WicaPeriod, str(p.id)):
                raise HTTPException(409, "Period ID is unavailable.")
            db.add(WicaPeriod(id=str(p.id), client_id=client_id, **data))
    audit(db, user, "settings_saved", client_id, body.model_dump(mode="json"))
    db.commit()
    return settings(user, db)


@router.get("/employees")
def employees(
    q: str = Query(min_length=2, max_length=100),
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> list[dict[str, str]]:
    rows = db.scalars(
        select(Employee)
        .where(
            Employee.client_id == require_client_id(user),
            or_(
                Employee.employee_name.icontains(q, autoescape=True),
                Employee.staff_id.icontains(q, autoescape=True),
            ),
        )
        .order_by(Employee.created_at.desc())
        .limit(30)
    ).all()
    return [
        {"id": r.id, "employee_name": r.employee_name or r.staff_id, "staff_id": r.staff_id}
        for r in rows
    ]


@router.get("/incidents")
def incidents(
    q: str = Query(default="", max_length=100),
    period_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    where = [WicaIncident.client_id == require_client_id(user)]
    if q:
        where.append(
            or_(
                WicaIncident.employee_name.icontains(q, autoescape=True),
                WicaIncident.staff_id.icontains(q, autoescape=True),
                WicaIncident.report_number.icontains(q, autoescape=True),
            )
        )
    if period_id:
        where.append(WicaIncident.period_id == period_id)
    rows = db.scalars(
        select(WicaIncident)
        .where(*where)
        .order_by(WicaIncident.incident_date.desc(), WicaIncident.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return {
        "items": [svc.summary(r) for r in rows],
        "total": db.scalar(select(func.count()).select_from(WicaIncident).where(*where)),
    }


@router.post("/incidents", status_code=201)
def create_incident(
    body: IncidentIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # Serialize intake with settings changes and retried incident creation.
    svc.enabled(db, user, lock=True)
    client_id = require_client_id(user)
    existing = db.get(WicaIncident, str(body.id))
    if existing:
        if existing.client_id != client_id:
            raise HTTPException(409, "Incident ID is unavailable.")
        if any(
            str(getattr(existing, k)) != str(v) for k, v in body.model_dump(exclude={"id"}).items()
        ):
            raise HTTPException(409, "Incident ID was already used for different details.")
        return svc.detail(db, existing)
    period = db.scalar(
        select(WicaPeriod).where(
            WicaPeriod.id == str(body.period_id), WicaPeriod.client_id == client_id
        )
    )
    if not period or not period.start_date <= body.incident_date <= period.end_date:
        raise HTTPException(422, "Incident date must fall within the selected WICA period.")
    if body.incident_date > today():
        raise HTTPException(422, "Incident date cannot be in the future.")
    if period.grace_days is not None and today() > period.end_date + timedelta(
        days=period.grace_days
    ):
        raise HTTPException(422, "The configured submission grace period has ended.")
    if body.employee_id:
        employee = db.scalar(
            select(Employee).where(Employee.id == body.employee_id, Employee.client_id == client_id)
        )
        if (
            not employee
            or employee.staff_id != body.staff_id
            or (employee.employee_name or employee.staff_id) != body.employee_name
        ):
            raise HTTPException(422, "Select an employee belonging to this company.")
    row = WicaIncident(
        **body.model_dump(exclude={"id", "period_id"}),
        id=str(body.id),
        period_id=str(body.period_id),
        client_id=client_id,
    )
    db.add(row)
    db.flush()
    audit(db, user, "incident_created", row.id)
    db.commit()
    return svc.detail(db, row)


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: str,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    return svc.detail(db, row)


@router.post("/incidents/{incident_id}/documents", status_code=201)
@limiter.limit("30/minute")
async def upload(
    incident_id: str,
    request: Request,
    file: UploadFile = File(),
    revision: int = Form(ge=1),
    document_id: UUID = Form(),
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    async with saved_upload(file, DOCUMENT_SUFFIXES, MAX_DOCUMENT_BYTES) as path:
        # One worker owns the session through commit/rollback. A competing
        # PostgreSQL lock wait must never block the loop that resumes its owner.
        # AnyIO shields this call until the worker finishes, keeping the temp
        # file and dependency session alive even if the request is cancelled.
        return await run_in_threadpool(
            _persist_upload, db, user, incident_id, revision, document_id, path, file.filename
        )


def _persist_upload(
    db: Session,
    user: CurrentUser,
    incident_id: str,
    revision: int,
    document_id: UUID,
    path: Path,
    original_filename: str | None,
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.enabled(db, user)
    with path.open("rb") as stream:
        mime = _sniff_mime(stream.read(16), path.suffix)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    scan_quarantined_document(path)
    svc.advance(db, row, revision)
    filename = Path((original_filename or "document").replace("\\", "/")).name[:255]
    duplicate = db.scalar(
        select(WicaDocument.id)
        .join(StoredDocument, WicaDocument.stored_document_id == StoredDocument.id)
        .where(
            WicaDocument.incident_id == row.id,
            StoredDocument.client_id == row.client_id,
            StoredDocument.sha256 == digest,
            StoredDocument.file_name == filename,
        )
        .limit(1)
    )
    if duplicate:
        audit(db, user, "upload_reused", row.id, {"document_id": duplicate})
        db.commit()
        return svc.detail(db, row)
    count = db.scalar(
        select(func.count()).select_from(WicaDocument).where(WicaDocument.incident_id == row.id)
    )
    if (count or 0) >= 100:
        raise HTTPException(422, "An incident can hold up to 100 documents.")
    if db.get(WicaDocument, str(document_id)):
        raise HTTPException(409, "This upload was already recorded. Refresh the incident.")
    storage = get_storage()
    stored_id = str(uuid4())
    target = document_path(
        user.broker_firm_id, row.client_id, "wica", row.id, stored_id, path.suffix
    )
    try:
        with path.open("rb") as stream:
            saved = storage.save(stream, target)
        stored = StoredDocument(
            id=stored_id,
            client_id=row.client_id,
            entity_type="wica",
            entity_id=row.id,
            file_name=filename,
            mime_type=mime,
            size_bytes=saved.size_bytes,
            sha256=saved.sha256,
            storage_path=saved.path,
            uploaded_by_user_id=user.user_id,
        )
        db.add(stored)
        db.flush()
        db.add(WicaDocument(id=str(document_id), incident_id=row.id, stored_document_id=stored.id))
        audit(db, user, "document_uploaded", row.id, {"document_id": str(document_id)})
        db.commit()
    except Exception:
        db.rollback()
        try:
            storage.delete(target)
        except Exception:
            logger.exception("WICA upload cleanup failed for blob %s", stored_id)
        raise
    return svc.detail(db, row)


@router.patch("/incidents/{incident_id}/documents/{document_id}")
def tag(
    incident_id: str,
    document_id: str,
    body: TagIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.advance(db, row, body.revision)
    doc = svc.document(db, row, document_id)
    if doc.status not in {"untagged", "supporting", "submitted"}:
        raise HTTPException(409, "Undo pending insurer approval before editing this document.")
    if doc.claim_id and body.benefit_type != doc.benefit_type:
        raise HTTPException(409, "A claim's benefit type cannot change after its ID is created.")
    related = list(dict.fromkeys(str(x) for x in body.related_ids))
    if related and (body.benefit_type == "Others" or doc.id in related):
        raise HTTPException(422, "Only a claim can link other documents.")
    for related_id in related:
        linked = svc.document(db, row, related_id)
        if linked.status == "untagged":
            raise HTTPException(422, "Tag supporting files before linking them.")
    for k, v in body.model_dump(exclude={"revision", "related_ids"}).items():
        setattr(doc, k, v)
    doc.related_ids = related
    if body.benefit_type == "Others":
        doc.status = "supporting"
    else:
        doc.claim_id = doc.claim_id or f"WICA-{uuid4().hex.upper()}"
        doc.status = "submitted"
    audit(
        db,
        user,
        "document_tagged",
        row.id,
        {"document_id": doc.id, "claim_id": doc.claim_id, **body.model_dump(mode="json")},
    )
    db.commit()
    return svc.detail(db, row)


@router.get("/incidents/{incident_id}/documents/{document_id}/download")
@limiter.limit("30/minute")
def download_document(
    incident_id: str,
    document_id: str,
    request: Request,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> Response:
    row = svc.incident(db, user, incident_id)
    doc = svc.document(db, row, document_id)
    stored = db.get(StoredDocument, doc.stored_document_id)
    if (
        not stored
        or stored.client_id != row.client_id
        or stored.entity_type != "wica"
        or stored.entity_id != row.id
    ):
        raise HTTPException(404, "Document not found")
    try:
        content = get_storage().read(stored.storage_path)
    except Exception:
        raise HTTPException(503, "File unavailable. Try again later.") from None
    if hashlib.sha256(content).hexdigest() != stored.sha256:
        raise HTTPException(409, "File integrity check failed. Contact your administrator.")
    write_audit(
        db,
        user,
        "wica.document_downloaded",
        "wica",
        row.id,
        after={"document_id": doc.id},
        request=request,
    )
    db.commit()
    return Response(
        content,
        media_type=stored.mime_type,
        headers={
            "Content-Disposition": attachment_header(stored.file_name),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/incidents/{incident_id}/documents/{document_id}/remove")
def remove_untagged(
    incident_id: str,
    document_id: str,
    body: RevisionIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.advance(db, row, body.revision)
    doc = svc.document(db, row, document_id)
    if doc.status != "untagged":
        raise HTTPException(
            409, "Only untagged uploads can be removed. Tagged documents are retained."
        )
    # Keep bytes and their metadata for retention; only remove the unused UI row.
    audit(
        db,
        user,
        "untagged_upload_removed",
        row.id,
        {"document_id": doc.id, "stored_document_id": doc.stored_document_id},
    )
    db.delete(doc)
    db.commit()
    return svc.detail(db, row)


@router.post("/incidents/{incident_id}/documents/{document_id}/status")
def change_status(
    incident_id: str,
    document_id: str,
    body: ActionIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.advance(db, row, body.revision)
    doc = svc.document(db, row, document_id)
    allowed = {
        "submitted": {"pending_insurer", "rejected"},
        "pending_insurer": {"submitted", "settled", "rejected"},
    }
    if not doc.claim_id or body.status not in allowed.get(doc.status, set()):
        raise HTTPException(409, "This status change is not available. Refresh the incident.")
    if body.status == "settled":
        if body.settlement_date is None or body.settlement_amount is None:
            raise HTTPException(422, "Enter the insurer settlement date and amount.")
        if not row.incident_date <= body.settlement_date <= today():
            raise HTTPException(422, "Settlement date must be between the incident date and today.")
        doc.settlement_amount = body.settlement_amount
        doc.settlement_date = body.settlement_date
    elif body.settlement_amount is not None or body.settlement_date is not None:
        raise HTTPException(422, "Settlement values are only accepted when settling a claim.")
    if body.status == "rejected" and not body.remarks:
        raise HTTPException(422, "Enter the rejection reason.")
    previous = doc.status
    doc.status = body.status
    doc.insurer_reference = body.insurer_reference
    doc.remarks = body.remarks
    audit(
        db,
        user,
        "status_changed",
        row.id,
        {"document_id": doc.id, "from": previous, **body.model_dump(mode="json")},
    )
    db.commit()
    return svc.detail(db, row)


@router.post("/incidents/{incident_id}/packs", status_code=201)
def create_pack(
    incident_id: str,
    body: PackIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.advance(db, row, body.revision)
    ids = set(str(x) for x in body.document_ids)
    docs = [svc.document(db, row, i) for i in ids]
    if not any(d.claim_id for d in docs) or any(
        d.claim_id and d.status != "pending_insurer" for d in docs
    ):
        raise HTTPException(409, "Select at least one claim marked Pending insurer approval.")
    for doc in list(docs):
        for related_id in doc.related_ids:
            if related_id not in ids:
                ids.add(related_id)
                docs.append(svc.document(db, row, related_id))
    if any(d.status == "untagged" for d in docs):
        raise HTTPException(422, "Tag all selected documents before preparing a pack.")
    manifest = []
    total_size = 0
    for doc in sorted(docs, key=lambda d: d.id):
        stored = db.get(StoredDocument, doc.stored_document_id)
        if not stored or stored.client_id != row.client_id or stored.entity_id != row.id:
            raise HTTPException(404, "Document not found")
        total_size += stored.size_bytes
        manifest.append(
            {
                "document_id": doc.id,
                "stored_document_id": stored.id,
                "file_name": stored.file_name,
                "sha256": stored.sha256,
                "claim_id": doc.claim_id,
                "doc_type": doc.doc_type,
                "benefit_type": doc.benefit_type,
                "document_date": str(doc.document_date),
            }
        )
    if total_size > 50 * 1024 * 1024:
        raise HTTPException(422, "Selected files exceed 50 MB. Prepare smaller packs.")
    if db.get(WicaPack, str(body.id)):
        raise HTTPException(409, "Pack already prepared. Refresh the incident.")
    db.add(
        WicaPack(id=str(body.id), incident_id=row.id, manifest=manifest, created_by=user.user_id)
    )
    audit(db, user, "pack_prepared", row.id, {"pack_id": str(body.id), "document_ids": sorted(ids)})
    db.commit()
    return svc.detail(db, row)


def pack_for(db: Session, row: WicaIncident, pack_id: str) -> WicaPack:
    pack = db.scalar(select(WicaPack).where(WicaPack.id == pack_id, WicaPack.incident_id == row.id))
    if pack is None:
        raise HTTPException(404, "Pack not found")
    return pack


@router.get("/incidents/{incident_id}/packs/{pack_id}/download")
@limiter.limit("10/minute")
def download_pack(
    incident_id: str,
    pack_id: str,
    request: Request,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> Response:
    row = svc.incident(db, user, incident_id)
    pack = pack_for(db, row, pack_id)
    storage = get_storage()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, entry in enumerate(pack.manifest, 1):
            stored = db.get(StoredDocument, entry["stored_document_id"])
            if (
                not stored
                or stored.client_id != row.client_id
                or stored.entity_id != row.id
                or stored.entity_type != "wica"
            ):
                raise HTTPException(404, "Document not found")
            try:
                content = storage.read(stored.storage_path)
            except Exception:
                logger.exception("WICA retained file unavailable %s", stored.id)
                raise HTTPException(
                    503, "A file is unavailable. Try the download again later."
                ) from None
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise HTTPException(
                    409, "A file failed its integrity check. Contact your administrator."
                )
            name = re.sub(r"[^\w. -]", "_", entry["file_name"])
            archive.writestr(f"{index:03d}-{name}", content)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {"incident_id": row.id, "pack_id": pack.id, "documents": pack.manifest}, indent=2
            ),
        )
    write_audit(
        db,
        user,
        "wica.pack_downloaded",
        "wica",
        row.id,
        after={"pack_id": pack.id},
        request=request,
    )
    db.commit()
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": attachment_header(f"WICA-{pack.id}.zip"),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/incidents/{incident_id}/packs/{pack_id}/sent")
def mark_sent(
    incident_id: str,
    pack_id: str,
    body: SentIn,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = svc.incident(db, user, incident_id)
    svc.advance(db, row, body.revision)
    pack = pack_for(db, row, pack_id)
    if pack.sent_on:
        raise HTTPException(409, "This pack is already recorded as sent.")
    if not row.incident_date <= body.sent_on <= today():
        raise HTTPException(422, "Sent date must be between the incident date and today.")
    pack.sent_on, pack.sent_reference = body.sent_on, body.sent_reference
    audit(
        db,
        user,
        "pack_sent_externally",
        row.id,
        {"pack_id": pack.id, **body.model_dump(mode="json")},
    )
    db.commit()
    return svc.detail(db, row)
