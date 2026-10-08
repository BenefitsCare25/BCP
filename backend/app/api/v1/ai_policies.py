"""Platform-wide policy library: system admins publish; broker roles read."""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_system_admin
from app.core.downloads import attachment_header
from app.core.storage import get_storage
from app.core.uploads import saved_upload
from app.db.base import new_uuid
from app.db.session import get_db
from app.models.ai_policy import AIPolicyVersion
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ai-policies", tags=["ai-policies"])
Category = Literal["AI usage policy", "Data handling", "Operating procedure", "Other"]
MAX_POLICY_BYTES = 10 * 1024 * 1024


class PolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    policy_id: str
    version: int
    title: str
    category: str
    status: str
    review_due: date | None
    file_name: str
    size_bytes: int
    uploaded_by: str
    created_at: datetime
    published_by: str | None
    published_at: datetime | None
    archived_by: str | None
    archived_at: datetime | None


class PolicyPage(BaseModel):
    items: list[PolicyOut]
    has_more: bool


def reader(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role not in {"system_admin", "firm_admin", "broker_admin", "broker_viewer"}:
        raise HTTPException(403, "AI policies require a broker or system administrator role.")
    return user


def actor(db: Session, user: CurrentUser) -> str:
    identity = db.get(User, user.user_id)
    return (identity.display_name or identity.email) if identity else (user.email or user.user_id)


def load(db: Session, user: CurrentUser, version_id: str) -> AIPolicyVersion:
    row = db.get(AIPolicyVersion, version_id)
    if not row or (user.role != "system_admin" and row.published_at is None):
        raise HTTPException(404, "Policy version not found")
    return row


@router.get("", response_model=PolicyPage)
def list_policies(
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    include_archived: bool = Query(False),
    user: CurrentUser = Depends(reader),
    db: Session = Depends(get_db),
) -> PolicyPage:
    query = select(AIPolicyVersion)
    if not include_archived:
        query = query.where(AIPolicyVersion.status != "archived")
    if user.role != "system_admin":
        query = query.where(AIPolicyVersion.published_at.is_not(None))
    rows = list(db.scalars(query.order_by(
        AIPolicyVersion.created_at.desc(), AIPolicyVersion.id,
    ).offset(offset).limit(limit + 1)))
    return PolicyPage(items=[PolicyOut.model_validate(row) for row in rows[:limit]],
                      has_more=len(rows) > limit)


@router.post("", response_model=PolicyOut, status_code=201)
async def upload_policy(
    request: Request,
    title: str = Form(..., min_length=1, max_length=160),
    category: Category = Form("AI usage policy"),
    review_due: date | None = Form(None),
    previous_id: str | None = Form(None, max_length=36),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PolicyOut:
    if not title.strip():
        raise HTTPException(422, "Enter a policy title.")
    async with saved_upload(file, frozenset({".pdf"}), MAX_POLICY_BYTES) as path:
        with path.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise HTTPException(415, "Upload a PDF document.")
        return await run_in_threadpool(
            _save, db, user, request, path, file.filename or "policy.pdf",
            title.strip(), category, review_due, previous_id,
        )


def _save(
    db: Session, user: CurrentUser, request: Request, path: Path, filename: str,
    title: str, category: str, review_due: date | None, previous_id: str | None,
) -> PolicyOut:
    policy_id, version = new_uuid(), 1
    if previous_id:
        previous = load(db, user, previous_id)
        versions = list(db.scalars(select(AIPolicyVersion).where(
            AIPolicyVersion.policy_id == previous.policy_id,
        ).order_by(AIPolicyVersion.id).with_for_update()))
        policy_id = previous.policy_id
        version = max(row.version for row in versions) + 1
        title, category = previous.title, previous.category
    version_id = new_uuid()
    target = f"platform/ai-policies/{policy_id}/{version_id}.pdf"
    storage = get_storage()
    try:
        with path.open("rb") as stream:
            saved = storage.save(stream, target)
        row = AIPolicyVersion(
            id=version_id, policy_id=policy_id, version=version, title=title,
            category=category, review_due=review_due, status="draft",
            file_name=Path(filename.replace("\\", "/")).name[:255],
            storage_path=saved.path, sha256=saved.sha256, size_bytes=saved.size_bytes,
            uploaded_by=actor(db, user), uploaded_by_id=user.user_id,
        )
        db.add(row)
        write_audit(db, user, "ai_policy.uploaded", "ai_policy", version_id,
                    after={"policy_id": policy_id, "version": version}, request=request)
        db.flush()
        result = PolicyOut.model_validate(row)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        try:
            storage.delete(target)
        except Exception:
            logger.exception("Failed to remove incomplete policy upload %s", version_id)
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Another version was added. Refresh and retry.") from None
        logger.exception("Policy upload failed %s", version_id)
        raise HTTPException(503, "Could not save the policy. Try again.") from None


@router.post("/{version_id}/publish", response_model=PolicyOut)
def publish_policy(
    version_id: str,
    request: Request,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PolicyOut:
    row = load(db, user, version_id)
    versions = list(db.scalars(select(AIPolicyVersion).where(
        AIPolicyVersion.policy_id == row.policy_id,
    ).order_by(AIPolicyVersion.id).with_for_update().execution_options(populate_existing=True)))
    if row.status != "draft":
        raise HTTPException(409, "Only a draft version can be published. Refresh the library.")
    now, who = datetime.now(UTC), actor(db, user)
    for previous in versions:
        if previous.status == "published":
            previous.status, previous.archived_at, previous.archived_by = "archived", now, who
    db.flush()
    row.status, row.published_at, row.published_by = "published", now, who
    write_audit(db, user, "ai_policy.published", "ai_policy", row.id,
                after={"policy_id": row.policy_id, "version": row.version}, request=request)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Policy changed in another session. Refresh and retry.") from None
    db.refresh(row)
    return PolicyOut.model_validate(row)


@router.post("/{version_id}/archive", response_model=PolicyOut)
def archive_policy(
    version_id: str,
    request: Request,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PolicyOut:
    row = db.scalar(select(AIPolicyVersion).where(
        AIPolicyVersion.id == version_id,
    ).with_for_update())
    if not row:
        raise HTTPException(404, "Policy version not found")
    if row.status == "archived":
        raise HTTPException(409, "This version is already archived. Refresh the library.")
    row.status, row.archived_at, row.archived_by = "archived", datetime.now(UTC), actor(db, user)
    write_audit(db, user, "ai_policy.archived", "ai_policy", row.id, request=request)
    db.commit()
    db.refresh(row)
    return PolicyOut.model_validate(row)


@router.get("/{version_id}/download")
def download_policy(
    version_id: str,
    user: CurrentUser = Depends(reader),
    db: Session = Depends(get_db),
) -> Response:
    row = load(db, user, version_id)
    try:
        content = get_storage().read(row.storage_path)
    except Exception:
        raise HTTPException(503, "File unavailable. Try again later.") from None
    if hashlib.sha256(content).hexdigest() != row.sha256:
        raise HTTPException(409, "Document integrity check failed. Contact a system administrator.")
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": attachment_header(row.file_name),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })
