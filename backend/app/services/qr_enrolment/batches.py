"""HR download batches: a frozen ZIP of form PDFs, opened by link + PIN.

Preparation reads EVERY selected PDF before anything is published: one
missing or unreadable file fails the batch, so HR is never handed a partial
set. A grant is one link + PIN. The first browser that enters the PIN and taps
Start owns it for exactly ``ACCESS_SECONDS``, fixed in the database by an
atomic update — refreshes, retries, restarts and other API processes keep the
same deadline. A broker can issue a new link + PIN for the same prepared ZIP
until the batch expires; doing so revokes the previous one.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import secrets
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import IO, Any, BinaryIO, cast

import jwt
from fastapi import HTTPException, status
from openpyxl import Workbook
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.passwords import hash_password, verify_password
from app.core.settings import get_settings
from app.core.storage import StorageScopeError, company_firm_id, document_path, get_storage
from app.db.base import new_uuid
from app.models import (
    Employee,
    EnrollmentFormSubmission,
    HrDownloadBatch,
    HrDownloadGrant,
    PublicIntake,
    StoredDocument,
)
from app.models.qr_enrolment import (
    BATCH_CLOSED,
    BATCH_READY,
    GRANT_REVOKE_BATCH_CLOSED,
    GRANT_REVOKE_REISSUED,
    INTAKE_PENDING,
)
from app.models.stored_document import DOC_ENTITY_ENROL_FORM, STORAGE_AVAILABLE
from app.schemas.qr_enrolment import BatchFilterIn, BatchOut, BatchPreviewItemOut, GrantOut
from app.services.claims import assert_document_scope, track_pending_blob
from app.services.enrollment_forms.register import (
    FETCH_BATCH,
    FETCH_WORKERS,
    SAFE_NAME,
    read_scoped_blob,
)
from app.services.insurer_reports import autosize, bold_header
from app.services.qr_enrolment.access import hash_token, link_not_found, new_pin, new_token
from app.services.qr_enrolment.intake import DOC_ENTITY_QR_INTAKE
from app.services.xlsx_safe import append_safe

ACCESS_SECONDS = 600
UNUSED_HOURS = 24
BATCH_DAYS = 7
VERIFY_MINUTES = 30
MAX_PIN_FAILURES = 5
MAX_DOWNLOADS = 5
MAX_BATCH_FILES = 1000
MAX_BATCH_BYTES = 300 * 1024 * 1024
COOKIE_NAME = "inspro_hr_download"
COOKIE_PATH = "/api/v1/public/hr-download"
EXPIRED = "expired"


def now_utc() -> datetime:
    """The one clock every deadline here reads (tests replace it)."""
    return datetime.now(UTC)


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored instant here is UTC."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ── Selecting and preparing ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _Item:
    kind: str
    id: str
    reference_no: str
    version: int
    staff_id: str | None
    name: str | None
    source: str
    status: str
    submitted_at: datetime | None
    document_id: str | None


def _items(db: Session, client_id: str, policy_year_id: str, f: BatchFilterIn) -> list[_Item]:
    sub = EnrollmentFormSubmission
    conds = [
        sub.client_id == client_id, sub.policy_year_id == policy_year_id,
        sub.status.in_(f.statuses),
    ]
    if f.window_id:
        conds.append(sub.window_id == f.window_id)
    excluded = set(f.exclude_ids)
    out = [
        _Item("form", s.id, s.reference_no, s.version, e.staff_id, e.employee_name,
              s.source, s.status, s.signed_at or s.created_at, s.document_id)
        for s, e in db.execute(
            select(sub, Employee).join(Employee, Employee.id == sub.employee_id)
            .where(*conds).order_by(Employee.staff_id, sub.version)
        ).all()
        if s.id not in excluded
    ]
    if f.include_pending_qr:
        iconds = [
            PublicIntake.client_id == client_id,
            PublicIntake.policy_year_id == policy_year_id,
            PublicIntake.status == INTAKE_PENDING,
        ]
        if f.window_id:
            iconds.append(PublicIntake.window_id == f.window_id)
        out.extend(
            _Item("intake", i.id, i.reference_no, 1, i.claimed_staff_id, i.claimed_name,
                  "qr", "pending verification", i.created_at, i.document_id)
            for i in db.scalars(
                select(PublicIntake).where(*iconds).order_by(PublicIntake.reference_no)
            )
            if i.id not in excluded
        )
    return out


def preview(
    db: Session, client_id: str, policy_year_id: str, f: BatchFilterIn
) -> tuple[list[BatchPreviewItemOut], int]:
    items = _items(db, client_id, policy_year_id, f)
    return [
        BatchPreviewItemOut(
            id=i.id, kind=i.kind, reference_no=i.reference_no,  # type: ignore[arg-type]
            version=i.version, staff_id=i.staff_id, employee_name=i.name,
            source=i.source, status=i.status, submitted_at=i.submitted_at,
        )
        for i in items[:MAX_BATCH_FILES]
    ], len(items)


def _file_name(item: _Item) -> str:
    who = SAFE_NAME.sub("", f"{item.staff_id or ''} {item.name or ''}").strip()
    pending = " PENDING VERIFICATION" if item.kind == "intake" else ""
    return f"{item.reference_no}{pending} {who} v{item.version}.pdf".replace("  ", " ")


def _documents(db: Session, items: list[_Item]) -> dict[str, StoredDocument]:
    ids = [i.document_id for i in items if i.document_id]
    docs = {
        d.id: d for d in db.scalars(select(StoredDocument).where(StoredDocument.id.in_(ids)))
    } if ids else {}
    good: dict[str, StoredDocument] = {}
    for item in items:
        doc = docs.get(item.document_id or "")
        expected = DOC_ENTITY_ENROL_FORM if item.kind == "form" else DOC_ENTITY_QR_INTAKE
        if (
            doc is not None and doc.entity_type == expected and doc.entity_id == item.id
            and doc.storage_state == STORAGE_AVAILABLE
        ):
            good[item.id] = doc
    return good


def _manifest_workbook(items: list[_Item], names: dict[str, str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Forms in this download"
    append_safe(ws, ["Reference", "Version", "Source", "Status", "Staff ID", "Employee",
                     "Submitted", "File"])
    for i in items:
        stamp = aware(i.submitted_at)
        append_safe(ws, [
            i.reference_no, i.version, {"qr": "QR form"}.get(i.source, i.source.title()),
            i.status.title(), i.staff_id or "", i.name or "",
            stamp.replace(tzinfo=None) if stamp else "", names[i.id],
        ])
    bold_header(ws)
    autosize(ws)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def prepare_batch(
    db: Session, *, client_id: str, policy_year_id: str, f: BatchFilterIn, label: str,
    user_id: str,
) -> HrDownloadBatch:
    items = _items(db, client_id, policy_year_id, f)
    if not items:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No forms match this selection.")
    if len(items) > MAX_BATCH_FILES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "batch_too_large",
            "message": f"{len(items)} forms selected. Prepare at most {MAX_BATCH_FILES} at a "
            "time: choose one enrolment period or status, or untick some forms.",
        })
    docs = _documents(db, items)
    missing = [i.reference_no for i in items if i.id not in docs]
    if missing:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "batch_files_missing",
            "message": "Some forms have no PDF available, so nothing was prepared. Untick them "
            "or contact support.",
            "references": missing[:20],
        })
    firm_id = company_firm_id(db, client_id)
    names = {i.id: _file_name(i) for i in items}
    batch_id = new_uuid()
    spool = _build_zip(items, docs, names, firm_id, client_id)
    try:
        path = document_path(firm_id, client_id, "hr_batch", batch_id, new_uuid(), ".zip")
        blob = get_storage().save(cast(BinaryIO, spool), path)
    finally:
        spool.close()
    track_pending_blob(db, blob.path)
    now = now_utc()
    batch = HrDownloadBatch(
        id=batch_id,
        client_id=client_id,
        policy_year_id=policy_year_id,
        window_id=f.window_id,
        label=label,
        status=BATCH_READY,
        manifest=[
            {"kind": i.kind, "id": i.id, "reference_no": i.reference_no, "version": i.version,
             "file_name": names[i.id], "sha256": docs[i.id].sha256,
             "size_bytes": docs[i.id].size_bytes, "status": i.status}
            for i in items
        ],
        file_count=len(items),
        size_bytes=blob.size_bytes,
        zip_path=blob.path,
        zip_sha256=blob.sha256,
        created_by=user_id,
        expires_at=now + timedelta(days=BATCH_DAYS),
    )
    db.add(batch)
    db.flush()
    return batch


def _build_zip(
    items: list[_Item], docs: dict[str, StoredDocument], names: dict[str, str],
    firm_id: str, client_id: str,
) -> IO[bytes]:
    """Every file or nothing: the first unreadable or oversized step stops the
    batch with a clear error after the archive is closed cleanly."""
    read = partial(read_scoped_blob, broker_firm_id=firm_id, client_id=client_id)
    spool: IO[bytes] = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    failure: HTTPException | None = None
    written = 0
    with (
        ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool,
        zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as archive,
    ):
        for start in range(0, len(items), FETCH_BATCH):
            chunk = items[start:start + FETCH_BATCH]
            paths = [docs[i.id].storage_path for i in chunk]
            for item, content in zip(chunk, pool.map(read, paths), strict=True):
                failure = _zip_problem(item, content, written)
                if failure is not None:
                    break
                assert content is not None
                written += len(content)
                archive.writestr(names[item.id], content)
            if failure is not None:
                break
        if failure is None:
            archive.writestr("Forms in this download.xlsx", _manifest_workbook(items, names))
    if failure is not None:
        spool.close()
        raise failure
    spool.seek(0)
    return spool


def _zip_problem(item: _Item, content: bytes | None, written: int) -> HTTPException | None:
    if content is None:
        return HTTPException(status.HTTP_409_CONFLICT, {
            "code": "batch_files_missing",
            "message": f"The PDF for {item.reference_no} could not be read, so nothing was "
            "prepared. Try again or untick it.",
            "references": [item.reference_no],
        })
    if written + len(content) > MAX_BATCH_BYTES:
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, {
            "code": "batch_too_large",
            "message": "These forms are too large for one download. Prepare them in "
            "smaller batches.",
        })
    return None


# ── Grant state and broker actions ───────────────────────────────────────────


def batch_state(batch: HrDownloadBatch, now: datetime) -> str:
    if batch.status != BATCH_READY:
        return batch.status
    expires = aware(batch.expires_at)
    return EXPIRED if expires is not None and now >= expires else BATCH_READY


def retire_if_due(db: Session, batch: HrDownloadBatch) -> None:
    """Delete an expired batch's ZIP the first time anyone looks at it."""
    if batch.status == BATCH_READY and batch_state(batch, now_utc()) == EXPIRED:
        _drop_zip(batch)
        batch.status = EXPIRED
        _revoke_all(db, batch.id, GRANT_REVOKE_BATCH_CLOSED, None)


def _drop_zip(batch: HrDownloadBatch) -> None:
    if batch.zip_path:
        try:
            get_storage().delete(batch.zip_path)
        except FileNotFoundError:
            pass
        batch.zip_path = None


def grant_state(grant: HrDownloadGrant, now: datetime) -> str:
    if grant.revoked_at is not None:
        return "revoked"
    if grant.activated_at is not None:
        expires = aware(grant.expires_at)
        return "active" if expires is not None and now < expires else EXPIRED
    if grant.locked_at is not None:
        return "locked"
    unused = aware(grant.unused_expires_at)
    return EXPIRED if unused is not None and now >= unused else "ready"


def grant_out(grant: HrDownloadGrant, now: datetime) -> GrantOut:
    return GrantOut(
        id=grant.id,
        state=grant_state(grant, now),  # type: ignore[arg-type]
        recipient_note=grant.recipient_note,
        issued_at=grant.created_at,
        unused_expires_at=grant.unused_expires_at,
        activated_at=grant.activated_at,
        expires_at=grant.expires_at,
        downloads=grant.downloads,
        revoke_reason=grant.revoke_reason,
    )


def latest_grant(db: Session, batch_id: str) -> HrDownloadGrant | None:
    return db.execute(
        select(HrDownloadGrant).where(HrDownloadGrant.batch_id == batch_id)
        .order_by(HrDownloadGrant.created_at.desc(), HrDownloadGrant.id.desc()).limit(1)
    ).scalar_one_or_none()


def batch_out(db: Session, batch: HrDownloadBatch) -> BatchOut:
    now = now_utc()
    grant = latest_grant(db, batch.id)
    return BatchOut(
        id=batch.id, label=batch.label,
        status=batch_state(batch, now),  # type: ignore[arg-type]
        window_id=batch.window_id, file_count=batch.file_count, size_bytes=batch.size_bytes,
        created_at=batch.created_at, expires_at=batch.expires_at,
        grant=grant_out(grant, now) if grant is not None else None,
    )


def _revoke_all(db: Session, batch_id: str, reason: str, user_id: str | None) -> None:
    db.execute(
        update(HrDownloadGrant)
        .where(HrDownloadGrant.batch_id == batch_id, HrDownloadGrant.revoked_at.is_(None))
        .values(revoked_at=now_utc(), revoked_by=user_id, revoke_reason=reason)
        .execution_options(synchronize_session="fetch")
    )


def issue_grant(
    db: Session, batch: HrDownloadBatch, user_id: str, recipient_note: str | None
) -> tuple[HrDownloadGrant, str, str]:
    """A new link + PIN for a ready batch; earlier links of the batch stop."""
    retire_if_due(db, batch)
    if batch_state(batch, now_utc()) != BATCH_READY or not batch.zip_path:
        raise HTTPException(status.HTTP_409_CONFLICT, {
            "code": "batch_unavailable",
            "message": "This batch has closed or expired. Prepare a new batch.",
        })
    _revoke_all(db, batch.id, GRANT_REVOKE_REISSUED, user_id)
    token, pin = new_token(), new_pin()
    now = now_utc()
    unused = min(now + timedelta(hours=UNUSED_HOURS), aware(batch.expires_at) or now)
    grant = HrDownloadGrant(
        client_id=batch.client_id,
        batch_id=batch.id,
        token_hash=hash_token(token),
        pin_hash=hash_password(pin),
        recipient_note=(recipient_note or "").strip() or None,
        issued_by=user_id,
        unused_expires_at=unused,
    )
    db.add(grant)
    db.flush()
    return grant, token, pin


def revoke_grant(grant: HrDownloadGrant, user_id: str, reason: str) -> None:
    if grant.revoked_at is None:
        grant.revoked_at = now_utc()
        grant.revoked_by = user_id
        grant.revoke_reason = reason


def close_batch(db: Session, batch: HrDownloadBatch, user_id: str) -> None:
    _revoke_all(db, batch.id, GRANT_REVOKE_BATCH_CLOSED, user_id)
    _drop_zip(batch)
    if batch.status == BATCH_READY:
        batch.status = BATCH_CLOSED
        batch.closed_at = now_utc()
        batch.closed_by = user_id


# ── Public download (no login) ───────────────────────────────────────────────


def _cookie_key() -> bytes:
    secret = get_settings().portal_jwt_secret or "dev-only"
    return hmac.new(secret.encode("utf-8"), b"inspro-hr-download", hashlib.sha256).digest()


def session_cookie(grant_id: str, nonce: str, verified_at: datetime) -> str:
    return jwt.encode(
        {"typ": "hrdl", "gid": grant_id, "n": nonce, "v": int(verified_at.timestamp())},
        _cookie_key(), algorithm="HS256",
    )


def read_cookie(value: str | None, grant_id: str) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        claims = jwt.decode(value, _cookie_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "hrdl" or claims.get("gid") != grant_id:
        return None
    return claims


def cookie_grant_id(value: str | None) -> str | None:
    """The grant a download cookie was issued for, before the grant is loaded."""
    if not value:
        return None
    try:
        claims = jwt.decode(value, _cookie_key(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    gid = claims.get("gid")
    return gid if claims.get("typ") == "hrdl" and isinstance(gid, str) else None


def resolve_grant(
    db: Session, raw: str, firm_id: str | None
) -> tuple[HrDownloadGrant, HrDownloadBatch]:
    """The grant behind a link, only within the host's firm (see
    ``intake.resolve_entry``). A 256-bit token has no useful existence oracle,
    so an unknown link costs nothing extra."""
    grant = db.execute(
        select(HrDownloadGrant).where(HrDownloadGrant.token_hash == hash_token(raw))
    ).scalar_one_or_none()
    return _grant_in_firm(db, grant, firm_id)


def grant_by_id(
    db: Session, grant_id: str, firm_id: str | None
) -> tuple[HrDownloadGrant, HrDownloadBatch]:
    return _grant_in_firm(db, db.get(HrDownloadGrant, grant_id), firm_id)


def _grant_in_firm(
    db: Session, grant: HrDownloadGrant | None, firm_id: str | None
) -> tuple[HrDownloadGrant, HrDownloadBatch]:
    batch = db.get(HrDownloadBatch, grant.batch_id) if grant is not None else None
    if grant is None or batch is None or (
        firm_id is not None and company_firm_id(db, grant.client_id) != firm_id
    ):
        raise link_not_found()
    retire_if_due(db, batch)
    return grant, batch


def visitor_state(
    grant: HrDownloadGrant, batch: HrDownloadBatch, claims: dict[str, Any] | None
) -> str:
    now = now_utc()
    if batch_state(batch, now) != BATCH_READY:
        return "revoked"
    state = grant_state(grant, now)
    if state == "active":
        nonce = (claims or {}).get("n")
        mine = bool(nonce) and hmac.compare_digest(
            hash_token(str(nonce)), grant.session_hash or ""
        )
        return "active" if mine else "in_use"
    if state == "ready" and claims is not None:
        verified = datetime.fromtimestamp(int(claims.get("v", 0)), UTC)
        if now - verified <= timedelta(minutes=VERIFY_MINUTES):
            return "verified"
    return state


def lock_grant(db: Session, grant: HrDownloadGrant) -> HrDownloadGrant:
    """Re-read the grant under a row lock, so concurrent PIN guesses are
    counted one after another and none slips past the lockout."""
    return db.execute(
        select(HrDownloadGrant).where(HrDownloadGrant.id == grant.id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def check_pin(db: Session, grant: HrDownloadGrant, pin: str) -> int | None:
    """None when the PIN is right; else the attempts left (0 = now locked).
    The caller holds the grant's row lock (``lock_grant``)."""
    if verify_password(grant.pin_hash, pin):
        return None
    failures = db.execute(
        update(HrDownloadGrant)
        .where(HrDownloadGrant.id == grant.id)
        .values(pin_failures=HrDownloadGrant.pin_failures + 1)
        .returning(HrDownloadGrant.pin_failures)
    ).scalar_one()
    if failures >= MAX_PIN_FAILURES:
        db.execute(
            update(HrDownloadGrant)
            .where(HrDownloadGrant.id == grant.id, HrDownloadGrant.locked_at.is_(None))
            .values(locked_at=now_utc())
        )
    db.refresh(grant)
    return max(0, MAX_PIN_FAILURES - int(failures))


def new_session() -> str:
    return secrets.token_urlsafe(24)


def activate(db: Session, grant: HrDownloadGrant, nonce: str) -> bool:
    """Claim the grant for this browser. True when this browser owns it —
    first claim or an idempotent retry of its own claim."""
    now = now_utc()
    mine = hash_token(nonce)
    claimed = db.execute(
        update(HrDownloadGrant)
        .where(
            HrDownloadGrant.id == grant.id,
            HrDownloadGrant.activated_at.is_(None),
            HrDownloadGrant.revoked_at.is_(None),
            HrDownloadGrant.locked_at.is_(None),
        )
        .values(
            activated_at=now, expires_at=now + timedelta(seconds=ACCESS_SECONDS),
            session_hash=mine,
        )
        .returning(HrDownloadGrant.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.refresh(grant)
    return claimed is not None or hmac.compare_digest(grant.session_hash or "", mine)


def claim_download(db: Session, grant: HrDownloadGrant) -> bool:
    """Count one download, atomically, only while under the limit."""
    counted = db.execute(
        update(HrDownloadGrant)
        .where(HrDownloadGrant.id == grant.id, HrDownloadGrant.downloads < MAX_DOWNLOADS)
        .values(downloads=HrDownloadGrant.downloads + 1, last_download_at=now_utc())
        .returning(HrDownloadGrant.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.refresh(grant)
    return counted is not None


def download_bytes(db: Session, batch: HrDownloadBatch) -> bytes:
    if not batch.zip_path:
        raise HTTPException(status.HTTP_410_GONE, "This download is no longer available.")
    try:
        assert_document_scope(batch.zip_path, company_firm_id(db, batch.client_id), batch.client_id)
        return get_storage().read(batch.zip_path)
    except (FileNotFoundError, StorageScopeError):
        raise HTTPException(
            status.HTTP_410_GONE, "This download is no longer available."
        ) from None
