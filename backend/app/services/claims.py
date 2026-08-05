"""Claims domain logic — creation, document attachment, submit validation,
broker decisions. Shared by the portal (member) and broker routers so the
rules live in one place.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
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
    CASE_TYPE_CLAIM,
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_SUBMITTED,
    LIVE_STATUSES,
    ORIGIN_PORTAL,
    VALID_TRANSITIONS,
)
from app.models.policy_year import PolicyYearStatus
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.api import BenefitStatementOut
from app.schemas.claims import ClaimCreateIn, ClaimOut, DocSlotOut, StoredDocumentOut
from app.services.claim_intake import (
    DOC_SLOT_LABELS,
    GP_SUB_TYPES,
    assert_documents_satisfy_slots,
    assert_intake_valid,
    benefit_row_for_sub_type,
    claim_profile_for,
    normalize_sub_type,
    required_doc_slots,
    resolve_sp_referral,
)
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


def load_member_claim(db: Session, claim_id: str, employee_id: str) -> Claim:
    """The member's own claim, or 404. THE point-load chokepoint for every
    member-facing endpoint that takes a claim id — the claim surface and the
    message surface both go through this one function, because they used to
    have a copy each and only one of them learned about broker-created cases.

    A claim belonging to a co-worker, and a case an assessor recorded from an
    email, are both simply "not found" — the same not-403 convention as tenant
    scoping, so the portal can't be used to discover what exists.
    """
    claim = db.get(Claim, claim_id)
    if claim is None or claim.employee_id != employee_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    if claim.origin != ORIGIN_PORTAL:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    return claim


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
    documents: dict[str, list[StoredDocument]] | None = None,
) -> ClaimOut:
    """Fill the derived fields every claim payload carries (documents, referral
    letter, claimant name). Shared by the member `claim_to_out` and the broker
    `_broker_out` so the two surfaces can't drift.

    ``referral_docs`` / ``dep_names`` / ``documents`` are optional per-page
    lookups the list endpoints prefetch in one query each, so rendering N claims
    doesn't fan out into N document + N referral + N dependant point-loads.
    **Every list endpoint must pass them** (`prefetch_claim_relations` returns
    all three) — the page size is 200, so the fan-out is the difference between
    3 queries and ~400."""
    docs = (
        documents.get(claim.id, [])
        if documents is not None
        else claim_documents(db, claim)
    )
    out.documents = [StoredDocumentOut.model_validate(d) for d in docs]
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
    # The document slots this claim must fill (resolved from its own fields, so
    # a needs_info/draft edit surface can render tagged uploads that match what
    # submit enforces). Same helper the coverage-options form uses.
    out.required_doc_slots = [
        DocSlotOut(key=k, label=DOC_SLOT_LABELS[k])
        for k in required_doc_slots(
            claim.product_code,
            claim.sub_type,
            claim.provider_name,
            claim_kind=claim.claim_kind,
        )
    ]
    if claim.dependant_id:
        out.dependant_name = (
            dep_names.get(claim.dependant_id)
            if dep_names is not None
            else dependant_display_name(db.get(Dependant, claim.dependant_id))
        )
    return out


def claim_documents_for(
    db: Session, claim_ids: list[str]
) -> dict[str, list[StoredDocument]]:
    """``{claim_id: [documents]}`` for a page of claims, in ONE query.

    The per-claim `claim_documents` call is the dominant cost of rendering a
    claims list: every claim carries at least one receipt, so a page of N claims
    issued N queries on top of everything else. Ordering matches
    `claim_documents` so a batched page renders identically to a single claim.
    """
    if not claim_ids:
        return {}
    out: dict[str, list[StoredDocument]] = {cid: [] for cid in claim_ids}
    for d in db.execute(
        select(StoredDocument)
        .where(
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id.in_(claim_ids),
        )
        .order_by(StoredDocument.created_at)
    ).scalars():
        out.setdefault(d.entity_id, []).append(d)
    return out


def prefetch_claim_relations(
    db: Session, claims: list[Claim]
) -> tuple[dict[str, StoredDocument], dict[str, str | None], dict[str, list[StoredDocument]]]:
    """One query each for the documents, referral letters and dependant names a
    page of claims references — pass the results to `populate_claim_out` to
    avoid the per-claim point-loads."""
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
    return referral_docs, dep_names, claim_documents_for(db, [c.id for c in claims])


def delete_stored_document(db: Session, doc: StoredDocument) -> None:
    """Remove a single stored document (bytes + row). Caller commits."""
    try:
        get_storage().delete(doc.storage_path)
    except Exception:
        # The blob delete failed but we still drop the row so the caller's flow
        # (claim/dependant deletion) isn't blocked by a transient storage
        # outage. That orphans a PII-bearing blob with no DB anchor, so log at
        # ERROR with enough context (doc id, client, path) for an ops
        # storage-vs-DB reconciliation sweep to find it later.
        logger.error(
            "Orphaned stored-document blob after failed delete: "
            "doc_id=%s client_id=%s path=%s",
            doc.id, doc.client_id, doc.storage_path, exc_info=True,
        )
    db.delete(doc)


def claim_to_out(db: Session, claim: Claim) -> ClaimOut:
    return populate_claim_out(db, claim, ClaimOut.model_validate(claim))


def claims_to_out(db: Session, claims: list[Claim]) -> list[ClaimOut]:
    """A whole PAGE of member-facing claims, with the relations batched.

    Use this for every list endpoint instead of mapping `claim_to_out`: that
    one is the single-claim path and point-loads each claim's documents,
    referral letter and dependant name. At the portal's page size (200) the
    difference is ~400 queries per claims-tab open versus three."""
    referral_docs, dep_names, documents = prefetch_claim_relations(db, claims)
    return [
        populate_claim_out(
            db,
            c,
            ClaimOut.model_validate(c),
            referral_docs=referral_docs,
            dep_names=dep_names,
            documents=documents,
        )
        for c in claims
    ]


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

    # Canonical sub-type label (folds pre-rename values from stale clients).
    sub_type = normalize_sub_type(body.sub_type)

    # Specialist claims: resolve the referral letter from the visit type
    # (first → must name one; follow-up → auto-link the latest on file).
    referral_document_id = body.referral_document_id
    if (
        body.claim_kind == CLAIM_KIND_INSURED
        and claim_profile_for(body.product_code).requires_referral
    ):
        referral_document_id = resolve_sp_referral(
            db,
            employee,
            visit_type=body.visit_type,
            referral_document_id=referral_document_id,
        )

    # Profile-driven intake rules (sub-type / diagnosis / referral / currency).
    assert_intake_valid(
        db,
        employee,
        claim_kind=body.claim_kind,
        product_code=body.product_code,
        sub_type=sub_type,
        diagnosis=body.diagnosis,
        referral_document_id=referral_document_id,
        referral_not_applicable=body.referral_not_applicable,
        currency=body.currency,
        visit_type=body.visit_type,
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
        sub_type=sub_type,
        visit_type=body.visit_type,
        incurred_date=body.incurred_date,
        provider_name=body.provider_name,
        invoice_number=body.invoice_number,
        diagnosis=body.diagnosis,
        remarks=body.remarks,
        amount_claimed=body.amount_claimed,
        currency=body.currency.upper(),
        referral_document_id=referral_document_id,
        submitted_by_member_id=submitted_by_member_id,
        # Snapshot of the member-entered form — the AI review compares the
        # uploaded documents against exactly what was claimed at the time.
        # `remarks` is a free-text note, not a document-matched field.
        form_fields={
            "claim_type": body.claim_type,
            "sub_type": sub_type,
            "visit_type": body.visit_type,
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
    doc_type: str | None = None,
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
        doc_type=doc_type,
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
            # See delete_stored_document: keep the flow moving but log at ERROR
            # so an orphaned PII blob is discoverable for reconciliation.
            logger.error(
                "Orphaned stored-document blob after failed delete: "
                "doc_id=%s client_id=%s path=%s",
                doc.id, doc.client_id, doc.storage_path, exc_info=True,
            )
        db.delete(doc)


def _apply_gp_rider_benefit_key(statement: BenefitStatementOut, claim: Claim) -> None:
    """TCM/Physio claims ride on GP coverage: bind the claim to the plan's
    matching schedule row — 422 when the member's plan doesn't carry one — so
    utilization and the broker's over-limit approve guard track that row's
    limit. Runs before `assert_coverage_claimable`, which then re-validates
    the stamped `benefit_key` against the schedule like any other."""
    if claim.claim_kind != CLAIM_KIND_INSURED or claim.sub_type not in GP_SUB_TYPES:
        return
    line = next(
        (c for c in statement.coverage if c.product_code == claim.product_code),
        None,
    )
    row = benefit_row_for_sub_type(
        line.benefit_schedule if line is not None else None, claim.sub_type
    )
    if row is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Your {claim.product_code} plan does not include "
            f"{claim.sub_type} cover.",
        )
    claim.benefit_key = row


def assert_coverage_claimable(statement: BenefitStatementOut, claim: Claim) -> None:
    """The claimed coverage must exist in the member's own resolved statement.

    Shared by member submit and broker LOG-case creation, so the two can never
    disagree about what a member is covered for. The ONE rule that differs
    between them is `member_claimable`, gated on the case type below.
    """
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
        # Products settled outside the portal (Major Medical, term life,
        # personal accident, critical illness) aren't member-filed — reject
        # even a hand-crafted request that bypassed the hidden picker.
        #
        # A LOG case is EXEMPT: this gate exists to stop members self-filing
        # products the insurer settles directly, and an assessor recording a
        # case on the member's behalf is precisely what it was written to
        # exclude. Gated on the case type rather than on who called, because the
        # exemption is a property of the case, not of the request.
        if (
            claim.case_type == CASE_TYPE_CLAIM
            and not claim_profile_for(claim.product_code).member_claimable
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{claim.product_code} claims aren't submitted through the "
                "portal — please contact your broker.",
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


def _assert_no_duplicate_receipt(
    db: Session, claim: Claim, documents: list[StoredDocument] | None = None
) -> None:
    """A receipt already attached to another LIVE claim of this client is a
    resubmission signal → structured 409 the UI can explain. ``documents`` may
    be passed by a caller that already loaded them to avoid a second query."""
    docs = documents if documents is not None else claim_documents(db, claim)
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


def claim_period_window(
    db: Session, year: PolicyYear, claim_kind: str
) -> tuple[date, date, str]:
    """The window a claim's incurred date must fall inside, and its label.

    Flex claims are bounded by the flex scheme's effective window (which
    defaults to the policy year's span); insured claims by the policy year.
    ONE implementation, shared by member submit and broker LOG-case creation —
    two copies would eventually disagree about which dates are claimable.
    """
    if claim_kind == CLAIM_KIND_FLEX:
        start, end = flex_effective_window(db, year)
        return start, end, "flex scheme's effective period"
    return year.start_date, year.end_date, "policy year"


def assert_incurred_in_period(
    db: Session, year: PolicyYear, claim: Claim
) -> tuple[date, date, str]:
    """422 unless the claim's incurred date sits inside its period.

    Returns the window AND its label so a caller applying a deadline anchored to
    the same end date doesn't have to resolve the window a second time — for a
    flex claim that repeat costs another `flex_effective_window` read on every
    submit."""
    window_start, window_end, period_label = claim_period_window(
        db, year, claim.claim_kind
    )
    if not (window_start <= claim.incurred_date <= window_end):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The incurred date must fall within the {period_label} "
            f"({window_start.isoformat()} to {window_end.isoformat()}).",
        )
    return window_start, window_end, period_label


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
    _, window_end, period_label = assert_incurred_in_period(db, year, claim)

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
    # profile) change can't slip through with missing sub-type/referral. A
    # draft holding a pre-rename sub-type label is folded onto the current one.
    claim.sub_type = normalize_sub_type(claim.sub_type)
    # Re-validate the specialist referral at submit: a legacy draft that used
    # the removed "not applicable" escape (visit_type=None, no referral) is
    # caught here, and a follow-up draft that still has no referral_document_id
    # gets the latest letter on file linked. A draft that already names a
    # letter keeps it (the member's explicit choice at draft time).
    if (
        claim.claim_kind == CLAIM_KIND_INSURED
        and claim_profile_for(claim.product_code).requires_referral
    ):
        claim.referral_document_id = resolve_sp_referral(
            db,
            employee,
            visit_type=claim.visit_type,
            referral_document_id=claim.referral_document_id,
        )
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
        visit_type=claim.visit_type,
    )
    statement = build_member_statement(db, employee)
    _apply_gp_rider_benefit_key(statement, claim)
    assert_coverage_claimable(statement, claim)
    # Every required document slot must be filled (tagged uploads); the
    # generic invoice/receipt slot accepts any attached document. Load the
    # documents once and share with the duplicate-receipt check.
    documents = claim_documents(db, claim)
    assert_documents_satisfy_slots(
        required_doc_slots(
            claim.product_code,
            claim.sub_type,
            claim.provider_name,
            claim_kind=claim.claim_kind,
        ),
        documents,
    )
    _assert_no_duplicate_receipt(db, claim, documents)

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
