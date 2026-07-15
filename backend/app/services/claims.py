"""Claims domain logic — creation, document attachment, submit validation,
broker decisions. Shared by the portal (member) and broker routers so the
rules live in one place.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.storage import (
    DOCUMENT_SUFFIXES,
    MAX_DOCUMENT_BYTES,
    document_path,
    get_storage,
)
from app.core.uploads import saved_upload
from app.models import Claim, Dependant, Employee, PolicyYear, StoredDocument
from app.models.claim import (
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_SUBMITTED,
    LIVE_STATUSES,
    VALID_TRANSITIONS,
)
from app.models.policy_year import PolicyYearStatus
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.api import BenefitStatementOut
from app.schemas.claims import ClaimCreateIn, ClaimOut, StoredDocumentOut
from app.services.flex_membership import flex_effective_window
from app.services.member_statement import build_member_statement

logger = logging.getLogger(__name__)

# Magic-byte signatures for the allowed document types. The stored mime_type
# is derived from the actual bytes (falling back to the extension), never from
# the client-supplied Content-Type header.
_MAGIC_MIME: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8", "image/jpeg"),
)
_SUFFIX_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _sniff_mime(head: bytes, suffix: str) -> str:
    for magic, mime in _MAGIC_MIME:
        if head.startswith(magic):
            return mime
    return _SUFFIX_MIME.get(suffix, "application/octet-stream")


def claim_documents(db: Session, claim: Claim) -> list[StoredDocument]:
    return list(
        db.execute(
            select(StoredDocument)
            .where(
                StoredDocument.entity_type == DOC_ENTITY_CLAIM,
                StoredDocument.entity_id == claim.id,
            )
            .order_by(StoredDocument.created_at)
        ).scalars().all()
    )


def claim_to_out(db: Session, claim: Claim) -> ClaimOut:
    docs = claim_documents(db, claim)
    out = ClaimOut.model_validate(claim)
    out.documents = [StoredDocumentOut.model_validate(d) for d in docs]
    return out


def create_claim(
    db: Session,
    employee: Employee,
    body: ClaimCreateIn,
    *,
    submitted_by_member_id: str | None,
) -> Claim:
    if body.claim_kind == CLAIM_KIND_INSURED and not body.product_code:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "An insured claim must name the product it draws on.",
        )
    if body.claim_kind == CLAIM_KIND_FLEX and not body.flex_category_name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A flex claim must name the claimable benefit category.",
        )
    if body.dependant_id:
        dep = db.get(Dependant, body.dependant_id)
        if dep is None or dep.employee_id != employee.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found")

    claim = Claim(
        client_id=employee.client_id,
        policy_year_id=employee.policy_year_id,
        employee_id=employee.id,
        dependant_id=body.dependant_id,
        claim_kind=body.claim_kind,
        product_code=body.product_code,
        benefit_key=body.benefit_key,
        flex_category_name=body.flex_category_name,
        claim_type=body.claim_type,
        incurred_date=body.incurred_date,
        provider_name=body.provider_name,
        invoice_number=body.invoice_number,
        diagnosis=body.diagnosis,
        remarks=body.remarks,
        amount_claimed=body.amount_claimed,
        currency=body.currency.upper(),
        submitted_by_member_id=submitted_by_member_id,
        # Snapshot of the member-entered form — the AI review compares the
        # uploaded documents against exactly what was claimed at the time.
        # `remarks` is a free-text note, not a document-matched field.
        form_fields={
            "claim_type": body.claim_type,
            "incurred_date": body.incurred_date.isoformat(),
            "provider_name": body.provider_name,
            "invoice_number": body.invoice_number,
            "diagnosis": body.diagnosis,
            "amount_claimed": body.amount_claimed,
            "currency": body.currency.upper(),
        },
    )
    db.add(claim)
    db.flush()
    return claim


async def attach_document(
    db: Session,
    *,
    client_id: str,
    broker_firm_id: str | None,
    entity_type: str,
    entity_id: str,
    file: UploadFile,
    uploaded_by_member_id: str | None = None,
    uploaded_by_user_id: str | None = None,
) -> StoredDocument:
    """Persist an uploaded document to retained storage + metadata row.
    Does NOT commit — the caller owns the transaction."""
    from app.db.base import new_uuid

    doc_id = new_uuid()
    async with saved_upload(
        file, set(DOCUMENT_SUFFIXES), max_bytes=MAX_DOCUMENT_BYTES
    ) as tmp_path:
        suffix = Path(file.filename or "").suffix.lower()
        path = document_path(
            broker_firm_id, client_id, entity_type, entity_id, doc_id, suffix
        )
        with tmp_path.open("rb") as stream:
            head = stream.read(16)
            stream.seek(0)
            blob = get_storage().save(stream, path)

    doc = StoredDocument(
        id=doc_id,
        client_id=client_id,
        entity_type=entity_type,
        entity_id=entity_id,
        file_name=file.filename or f"document{suffix}",
        mime_type=_sniff_mime(head, suffix),
        size_bytes=blob.size_bytes,
        sha256=blob.sha256,
        storage_path=blob.path,
        uploaded_by_member_id=uploaded_by_member_id,
        uploaded_by_user_id=uploaded_by_user_id,
    )
    db.add(doc)
    return doc


def delete_documents(db: Session, entity_type: str, entity_id: str) -> None:
    """Remove an entity's stored documents (bytes + rows). Caller commits."""
    storage = get_storage()
    docs = db.execute(
        select(StoredDocument).where(
            StoredDocument.entity_type == entity_type,
            StoredDocument.entity_id == entity_id,
        )
    ).scalars().all()
    for doc in docs:
        try:
            storage.delete(doc.storage_path)
        except Exception:
            logger.warning("Failed to delete blob %s", doc.storage_path)
        db.delete(doc)


def _assert_coverage_claimable(statement: BenefitStatementOut, claim: Claim) -> None:
    """The claimed coverage must exist in the member's own resolved statement."""
    if claim.claim_kind == CLAIM_KIND_INSURED:
        line = next(
            (c for c in statement.coverage if c.product_code == claim.product_code),
            None,
        )
        if line is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"You have no {claim.product_code} coverage to claim against.",
            )
        if claim.benefit_key:
            items = (line.benefit_schedule or {}).get("items") or []
            names = {str(i.get("name", "")).strip().lower() for i in items if isinstance(i, dict)}
            if claim.benefit_key.strip().lower() not in names:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"'{claim.benefit_key}' is not a benefit item on your "
                    f"{claim.product_code} schedule.",
                )
        if claim.dependant_id and not line.covers_dependants:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Your {claim.product_code} coverage does not extend to dependants.",
            )
        return

    flex = statement.flex
    if flex is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "You have no flex wallet to claim against.",
        )
    wanted = (claim.flex_category_name or "").strip().lower()
    category = next(
        (c for c in flex.benefit_categories if c.name.strip().lower() == wanted),
        None,
    )
    if category is None or not category.claimable:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{claim.flex_category_name}' is not a claimable flex benefit for you.",
        )


def _assert_no_duplicate_receipt(db: Session, claim: Claim) -> None:
    """A receipt already attached to another LIVE claim of this client is a
    resubmission signal → structured 409 the UI can explain."""
    docs = claim_documents(db, claim)
    if not docs:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Attach at least one receipt before submitting.",
        )
    hashes = [d.sha256 for d in docs]
    dupes = db.execute(
        select(StoredDocument).where(
            StoredDocument.client_id == claim.client_id,
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id != claim.id,
            StoredDocument.sha256.in_(hashes),
        )
    ).scalars().all()
    if not dupes:
        return
    dupe_claim_ids = {d.entity_id for d in dupes}
    live = db.execute(
        select(Claim.id).where(
            Claim.id.in_(dupe_claim_ids), Claim.status.in_(LIVE_STATUSES)
        )
    ).scalars().all()
    if live:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_receipt",
                "message": "One of these receipts was already submitted on another claim.",
                "conflicting_claim_ids": sorted(live),
            },
        )


def submit_claim(
    db: Session,
    claim: Claim,
    employee: Employee,
    *,
    submitted_by_member_id: str | None,
) -> Claim:
    """Validate + move a claim to `submitted`. Caller commits (after audit)."""
    assert_transition(claim, CLAIM_STATUS_SUBMITTED)

    year = db.get(PolicyYear, claim.policy_year_id)
    if year is None or year.status != PolicyYearStatus.active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Claims can only be submitted against the active policy year.",
        )
    # Flex claims are bounded by the flex scheme's effective window (which
    # defaults to the policy year's span); insured claims by the policy year.
    if claim.claim_kind == CLAIM_KIND_FLEX:
        window_start, window_end = flex_effective_window(db, year)
        period_label = "flex scheme's effective period"
    else:
        window_start, window_end = year.start_date, year.end_date
        period_label = "policy year"
    if not (window_start <= claim.incurred_date <= window_end):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The incurred date must fall within the {period_label} "
            f"({window_start.isoformat()} to {window_end.isoformat()}).",
        )

    _assert_coverage_claimable(build_member_statement(db, employee), claim)
    _assert_no_duplicate_receipt(db, claim)

    claim.status = CLAIM_STATUS_SUBMITTED
    claim.submitted_at = datetime.now(UTC)
    if submitted_by_member_id:
        claim.submitted_by_member_id = submitted_by_member_id
    return claim


def assert_transition(claim: Claim, new_status: str) -> None:
    allowed = VALID_TRANSITIONS.get(claim.status, frozenset())
    if new_status not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "invalid_transition",
                "message": f"A {claim.status} claim cannot move to {new_status}.",
            },
        )
