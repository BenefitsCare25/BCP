"""Claims domain logic — creation, document attachment, submit validation,
broker decisions. Shared by the portal (member) and broker routers so the
rules live in one place.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
from app.services.claim_intake import assert_intake_valid
from app.services.flex_membership import flex_effective_window
from app.services.member_statement import build_member_statement
from app.services.roster_attributes import NAME_KEYS, first_value

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


def dependant_display_name(dep: Dependant | None) -> str | None:
    if dep is None:
        return None
    return first_value(dep.attribute_values or {}, NAME_KEYS)


def populate_claim_out(
    db: Session,
    claim: Claim,
    out: ClaimOut,
    *,
    referral_docs: dict[str, StoredDocument] | None = None,
    dep_names: dict[str, str | None] | None = None,
) -> ClaimOut:
    """Fill the derived fields every claim payload carries (documents, referral
    letter, claimant name). Shared by the member `claim_to_out` and the broker
    `_broker_out` so the two surfaces can't drift.

    ``referral_docs`` / ``dep_names`` are optional per-page lookups the list
    endpoints prefetch in one query each, so rendering N claims doesn't fan out
    into N referral + N dependant point-loads."""
    out.documents = [
        StoredDocumentOut.model_validate(d) for d in claim_documents(db, claim)
    ]
    if claim.referral_document_id:
        ref = (
            referral_docs.get(claim.referral_document_id)
            if referral_docs is not None
            else db.get(StoredDocument, claim.referral_document_id)
        )
        if ref is not None:
            out.referral_document = StoredDocumentOut.model_validate(ref)
    out.referral_not_applicable = bool(
        (claim.form_fields or {}).get("referral_not_applicable")
    )
    if claim.dependant_id:
        out.dependant_name = (
            dep_names.get(claim.dependant_id)
            if dep_names is not None
            else dependant_display_name(db.get(Dependant, claim.dependant_id))
        )
    return out


def prefetch_claim_relations(
    db: Session, claims: list[Claim]
) -> tuple[dict[str, StoredDocument], dict[str, str | None]]:
    """One query each for the referral letters + dependant names a page of
    claims references — pass the results to `populate_claim_out` to avoid the
    per-claim point-loads."""
    referral_ids = {c.referral_document_id for c in claims if c.referral_document_id}
    dep_ids = {c.dependant_id for c in claims if c.dependant_id}
    referral_docs: dict[str, StoredDocument] = {}
    if referral_ids:
        for d in db.execute(
            select(StoredDocument).where(StoredDocument.id.in_(referral_ids))
        ).scalars():
            referral_docs[d.id] = d
    dep_names: dict[str, str | None] = {}
    if dep_ids:
        for dep in db.execute(
            select(Dependant).where(Dependant.id.in_(dep_ids))
        ).scalars():
            dep_names[dep.id] = dependant_display_name(dep)
    return referral_docs, dep_names


def delete_stored_document(db: Session, doc: StoredDocument) -> None:
    """Remove a single stored document (bytes + row). Caller commits."""
    try:
        get_storage().delete(doc.storage_path)
    except Exception:
        logger.warning("Failed to delete blob %s", doc.storage_path)
    db.delete(doc)


def claim_to_out(db: Session, claim: Claim) -> ClaimOut:
    return populate_claim_out(db, claim, ClaimOut.model_validate(claim))


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
        # Portal self-added dependants stay pending until broker approval —
        # they aren't covered, so they can't be claimed for (mirrors the
        # benefit statement's active-only filter).
        if dep.status != "active":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "This dependant is pending your broker's approval and can't "
                "be claimed for yet.",
            )

    # Profile-driven intake rules (sub-type / diagnosis / referral / currency).
    assert_intake_valid(
        db,
        employee,
        claim_kind=body.claim_kind,
        product_code=body.product_code,
        sub_type=body.sub_type,
        diagnosis=body.diagnosis,
        referral_document_id=body.referral_document_id,
        referral_not_applicable=body.referral_not_applicable,
        currency=body.currency,
    )

    claim = Claim(
        client_id=employee.client_id,
        policy_year_id=employee.policy_year_id,
        employee_id=employee.id,
        dependant_id=body.dependant_id,
        claim_kind=body.claim_kind,
        product_code=body.product_code,
        flex_category_name=body.flex_category_name,
        claim_type=body.claim_type,
        sub_type=body.sub_type,
        incurred_date=body.incurred_date,
        provider_name=body.provider_name,
        invoice_number=body.invoice_number,
        diagnosis=body.diagnosis,
        remarks=body.remarks,
        amount_claimed=body.amount_claimed,
        currency=body.currency.upper(),
        referral_document_id=body.referral_document_id,
        submitted_by_member_id=submitted_by_member_id,
        # Snapshot of the member-entered form — the AI review compares the
        # uploaded documents against exactly what was claimed at the time.
        # `remarks` is a free-text note, not a document-matched field.
        form_fields={
            "claim_type": body.claim_type,
            "sub_type": body.sub_type,
            "incurred_date": body.incurred_date.isoformat(),
            "provider_name": body.provider_name,
            "invoice_number": body.invoice_number,
            "diagnosis": body.diagnosis,
            "amount_claimed": body.amount_claimed,
            "currency": body.currency.upper(),
            "referral_not_applicable": body.referral_not_applicable,
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
        if claim.dependant_id:
            if not line.covers_dependants:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Your {claim.product_code} coverage does not extend to "
                    "dependants.",
                )
            # The statement's covered list is authoritative — an enrollment
            # override electing a subset (e.g. spouse only) must bind here
            # too, not just in the form's picker.
            if claim.dependant_id not in {d.id for d in line.covered_dependants}:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"This dependant is not covered under your "
                    f"{claim.product_code} plan.",
                )
        return

    flex = statement.flex
    if flex is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "You have no flex wallet to claim against.",
        )
    # Flex claims may draw down the member's wallet for a dependant's expense,
    # but only for an active (broker-approved) dependant on the statement;
    # scheme-specific family rules stay the broker's call at review.
    if claim.dependant_id and claim.dependant_id not in {
        d.id for d in statement.dependants
    }:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This dependant is not active on your record, so their expenses "
            "can't be claimed from your flex wallet.",
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

    # Submission grace period: once configured, claims can only be submitted up
    # to N days after the enforced period ends. Anchored to `window_end` (the
    # flex effective end for flex claims, the policy-year end otherwise) so the
    # grace lines up with the incurred-window check above. None = no deadline.
    if year.claim_grace_period_days is not None:
        deadline = window_end + timedelta(days=year.claim_grace_period_days)
        if datetime.now(tz=UTC).date() > deadline:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"The claim submission window for this {period_label} closed on "
                f"{deadline.isoformat()} (period end + "
                f"{year.claim_grace_period_days} days grace).",
            )

    # Re-run the intake rules at submit so a draft created before a rule (or
    # profile) change can't slip through with missing sub-type/referral.
    assert_intake_valid(
        db,
        employee,
        claim_kind=claim.claim_kind,
        product_code=claim.product_code,
        sub_type=claim.sub_type,
        diagnosis=claim.diagnosis,
        referral_document_id=claim.referral_document_id,
        referral_not_applicable=bool(
            (claim.form_fields or {}).get("referral_not_applicable")
        ),
        currency=claim.currency,
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
