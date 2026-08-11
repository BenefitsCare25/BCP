"""Claims domain logic — creation, document attachment, submit validation,
broker decisions. Shared by the portal (member) and broker routers so the
rules live in one place.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
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
    CASE_TYPE_LOG,
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_DRAFT,
    CLAIM_STATUS_REJECTED,
    CLAIM_STATUS_SUBMITTED,
    LIVE_STATUSES,
    MEMBER_EDITABLE_STATUSES,
    ORIGIN_PORTAL,
    SETTLED_STATUSES,
    VALID_TRANSITIONS,
)
from app.models.policy_year import PolicyYearStatus
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.api import BenefitStatementOut
from app.schemas.claims import ClaimCreateIn, ClaimOut, DocSlotOut, StoredDocumentOut
from app.services.claim_intake import (
    DOC_SLOT_LABELS,
    GP_SUB_TYPES,
    assert_doctor_name_valid,
    assert_documents_satisfy_slots,
    assert_intake_valid,
    benefit_row_for_sub_type,
    claim_profile_for,
    normalize_invoice_number,
    normalize_sub_type,
    required_doc_slots,
    resolve_sp_referral,
)
from app.services.claim_settlement import mint_reference_no
from app.services.flex_membership import flex_effective_window
from app.services.member_statement import build_member_statement
from app.services.roster_attributes import NAME_KEYS, cover_end, first_value

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


# Why the claimant may no longer change a claim, in their own words. One owner
# for the sentence as well as for the boolean beside it — the portal's lock
# notice reads this string, so a member cannot be given two different accounts
# of why the form is closed (same discipline as `ClaimWindow.empty_note`).
_EDIT_BLOCKED_LOG = (
    "Your broker is handling this case directly. Message us if something "
    "needs changing."
)
_EDIT_BLOCKED_SETTLED = (
    "This claim has been assessed, so it can no longer be changed here. "
    "Message us if something needs correcting."
)
_EDIT_BLOCKED_REJECTED = (
    "This claim was closed, so it can no longer be changed here. Message us "
    "if you think that's wrong."
)
_EDIT_BLOCKED_DEFAULT = (
    "This claim can no longer be changed here. Message us if something needs "
    "correcting."
)


def member_editability(claim: Claim) -> tuple[bool, str | None]:
    """Whether the CLAIMANT may still change this claim, and why not.

    **Served, never re-derived in the client.** `ClaimDetailLeaf` used to answer
    this itself with `status === "draft" || status === "needs_info"` — the same
    drift class as mirroring `PENDING_STATUSES` into TypeScript, and it silently
    stops agreeing with the server the day the window moves. It moved.

    Fail-closed by construction: everything outside `MEMBER_EDITABLE_STATUSES`
    is refused, and a status this function has no sentence for still gets the
    default one rather than falling through to editable. A new status is far
    more likely to be post-decision than pre-, and wrongly denying an edit costs
    a message to the broker while wrongly allowing one rewrites a settled claim.
    """
    # A case the member never filed. They cannot reach it at all
    # (`load_member_claim` 404s on it), so there is nothing to explain to them —
    # this is here for the BROKER payload, which shares this builder.
    if claim.origin != ORIGIN_PORTAL:
        return False, None
    # Their own submission, reclassified by an assessor. It stays VISIBLE to
    # them — the portal filters on origin precisely so reclassifying can't
    # retract someone's own record — but it stops being theirs to edit: a LOG
    # case is created outside `submit_claim` and may legitimately carry no
    # documents at all, so re-validating one would refuse it with "attach at
    # least one receipt" on a case they never attached to and cannot satisfy.
    if claim.case_type == CASE_TYPE_LOG:
        return False, _EDIT_BLOCKED_LOG
    if claim.status in MEMBER_EDITABLE_STATUSES:
        return True, None
    if claim.status in SETTLED_STATUSES:
        return False, _EDIT_BLOCKED_SETTLED
    if claim.status == CLAIM_STATUS_REJECTED:
        return False, _EDIT_BLOCKED_REJECTED
    return False, _EDIT_BLOCKED_DEFAULT


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
    # Filled HERE — in the one builder the member payload, the broker payload
    # and the broker's employee-view preview all share — so the three cannot
    # answer "may this member still edit?" differently.
    out.member_editable, out.member_edit_block = member_editability(claim)
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
    assert_doctor_name_valid(
        body.product_code, sub_type, body.doctor_name, claim_kind=body.claim_kind
    )

    doctor_name = (body.doctor_name or "").strip() or None

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
        doctor_name=doctor_name,
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
            # Only when stated — an empty key on every GP receipt would give the
            # review's rules a blank to reason about on claims that never ask
            # for one.
            **({"doctor_name": doctor_name} if doctor_name else {}),
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


def _apply_gp_rider_benefit_key(
    statement: BenefitStatementOut, claim: Claim, *, clear_rider_key: bool = False
) -> None:
    """TCM/Physio claims ride on GP coverage: bind the claim to the plan's
    matching schedule row — 422 when the member's plan doesn't carry one — so
    utilization and the broker's over-limit approve guard track that row's
    limit. Runs before `assert_coverage_claimable`, which then re-validates
    the stamped `benefit_key` against the schedule like any other.

    ``clear_rider_key`` handles the OTHER direction, which only an AMENDMENT can
    reach. This function used to only ever SET the key, so editing a GP-TCM
    claim back down to plain GP left `benefit_key="TCM"` in place — and that
    passes `assert_coverage_claimable` (TCM is a real row on the schedule) while
    utilization keeps drawing the claim against the TCM sub-limit instead of the
    GP one. Silent, and in the money.

    The flag is passed ONLY when the amendment actually changed `sub_type`, and
    never at submit: a legacy row carries a `benefit_key` from before the column
    stopped being populated, and blanking those on a `needs_info` resubmission
    would move their bucket for no reason at all.
    """
    is_rider = (
        claim.claim_kind == CLAIM_KIND_INSURED and claim.sub_type in GP_SUB_TYPES
    )
    if not is_rider:
        if clear_rider_key:
            # Also correct for an insured→flex amendment: a flex claim has no
            # business carrying an insured schedule row.
            claim.benefit_key = None
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


def duplicate_invoice_claim_ids(db: Session, claim: Claim) -> list[str]:
    """Live claims of the same member already claiming this claim's INVOICE.

    **The invoice number is what makes a claim a duplicate**, not the documents
    attached to it. The two are not the same test, and keying on the documents
    was wrong in both directions: a multi-invoice upload legitimately attaches
    ONE discharge summary (or itemised bill) to every claim of the same episode
    — the exact set the intake flow tells the member to upload together — so
    hash reuse blocked the second and third submissions of a genuine split;
    while a member who photographs the same bill twice produces two different
    hashes and sailed straight through.

    Scope is the member's OWN claims. `Employee` rows are per policy year, so
    that is this year's claims for their household (their own and their
    dependants'). Widening it to the whole company would let a stranger's
    identically-numbered receipt — short numeric receipt numbers do collide
    across providers — make a member's real claim unfileable, with no way for
    them to override it. Cross-member reuse is a fraud signal, not a member
    error, so it is surfaced to the broker by the review's deterministic rule
    (`claims_review/rules.py`) instead of blocking a submission.

    Shared by the submit gate and that rule so the two can't disagree.
    """
    key = normalize_invoice_number(claim.invoice_number)
    if not key:
        return []
    rows = db.execute(
        select(Claim.id, Claim.invoice_number).where(
            Claim.employee_id == claim.employee_id,
            Claim.id != claim.id,
            Claim.status.in_(LIVE_STATUSES),
        )
    ).all()
    return sorted(
        cid for cid, number in rows if normalize_invoice_number(number) == key
    )


def _assert_no_duplicate_invoice(
    db: Session, claim: Claim, documents: list[StoredDocument] | None = None
) -> None:
    """Structured 409 when this claim's invoice number is already on another
    live claim of the member's. ``documents`` may be passed by a caller that
    already loaded them to avoid a second query.

    **A hard refusal — there is deliberately no member-side override.** One
    invoice is one claim, full stop. The cost of that is real and worth knowing:
    a family clinic visit seen by the member AND their child is a single receipt
    that would have to be filed as two claims (the claimant is per-claim), and
    the member cannot change the number printed on the bill. That case now has
    no portal route at all — an assessor records it broker-side as a LOG case,
    which does not go through this path.
    """
    docs = documents if documents is not None else claim_documents(db, claim)
    if not docs:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Attach at least one receipt before submitting.",
        )
    live = duplicate_invoice_claim_ids(db, claim)
    if live:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_invoice_number",
                "message": (
                    f"Invoice {claim.invoice_number} has already been claimed. "
                    "Check the number against your receipt — if it's right, "
                    "that claim is already with us and there's nothing more to "
                    "send."
                ),
                "invoice_number": claim.invoice_number,
                "conflicting_claim_ids": live,
            },
        )


@dataclass(frozen=True)
class ClaimWindow:
    """The dates a claim may be incurred on, for one member.

    Two ends, deliberately. ``end`` is the latest date THIS MEMBER was covered
    on; ``period_end`` is where the period itself closes. They differ only for a
    leaver, and conflating them would make the submission grace period expire
    early for exactly those people — the grace deadline is a property of the
    year, not of when one person left.
    """

    start: date
    end: date
    label: str
    period_end: date
    period_label: str

    @property
    def is_empty(self) -> bool:
        """The member's cover ended BEFORE this period began, so nothing in it
        is claimable. Reachable two ways: a flex scheme that starts mid-year
        (CDL's starts 15 Jul) against someone who left in June, and a
        terminated row carried into a new benefit year with a prior-year last
        day. The range refuses every date on its own — `start <= d <= end` is
        unsatisfiable — but a caller PRINTING it has to know, or the form asks
        for "a date between 15 July and 1 June"."""
        return self.end < self.start

    @property
    def empty_note(self) -> str:
        """What to TELL someone whose window refuses every date.

        One sentence, one owner: the 422 below and the claim form's empty state
        both read it, so the member cannot be given two different accounts of
        why they have nothing to claim against."""
        return (
            f"Your cover ended on {self.end.isoformat()}, before this "
            f"{self.period_label} began — there is nothing to claim against "
            "here."
        )


def claim_period_window(
    db: Session, year: PolicyYear, claim_kind: str, employee: Employee
) -> ClaimWindow:
    """The window a claim's incurred date must fall inside, and its label.

    Flex claims are bounded by the flex scheme's effective window (which
    defaults to the policy year's span); insured claims by the policy year.
    ONE implementation, shared by member submit and broker LOG-case creation —
    two copies would eventually disagree about which dates are claimable.

    **A leaver's window closes on their last day.** Cover ending is a fact about
    the cover, not about the portal, so it belongs here rather than in the
    member-access gate: a broker recording a LOG case for a leaver is subject to
    the same truth, and putting the rule on one surface only is how the two
    would come to disagree about what is claimable.

    The bound comes from `roster_attributes.cover_end`, NOT `resolved_last_day`
    — a `Last Day of Service` sitting on an ACTIVE row is a template column
    nobody cleared, and reading it here would start refusing a live employee's
    claims. An unparseable date is likewise no bound at all (unknown →
    conservatively include).
    """
    if claim_kind == CLAIM_KIND_FLEX:
        start, end = flex_effective_window(db, year)
        label = "flex scheme's effective period"
    else:
        start, end = year.start_date, year.end_date
        label = "policy year"

    last_day = cover_end(employee)
    if last_day is not None and last_day < end:
        return ClaimWindow(start, last_day, "cover period", end, label)
    return ClaimWindow(start, end, label, end, label)


def assert_incurred_in_period(
    db: Session, year: PolicyYear, claim: Claim, employee: Employee
) -> ClaimWindow:
    """422 unless the claim's incurred date sits inside its period.

    Returns the resolved window so a caller applying a deadline anchored to the
    period's end doesn't have to resolve it a second time — for a flex claim
    that repeat costs another `flex_effective_window` read on every submit."""
    window = claim_period_window(db, year, claim.claim_kind, employee)
    if window.is_empty:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, window.empty_note
        )
    if not (window.start <= claim.incurred_date <= window.end):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The incurred date must fall within the {window.label} "
            f"({window.start.isoformat()} to {window.end.isoformat()}).",
        )
    return window


def validate_claim_facts(
    db: Session,
    claim: Claim,
    employee: Employee,
    *,
    enforce_doctor_name: bool,
    clear_rider_key: bool = False,
    window: ClaimWindow | None = None,
) -> ClaimWindow:
    """Everything submit checks about the CLAIM ITSELF. Returns its window.

    **The one validation chain, shared by submit and by both amendment
    endpoints.** Running it over the merged claim is what guarantees an EDITED
    claim is always a claim that would pass submit — change the product to one
    whose required documents aren't attached and the amendment is refused with
    the exact message submit would have given, so nothing invalid can land in
    the broker's queue.

    It deliberately EXCLUDES the two filing-window rules — the policy year being
    active, and the submission grace deadline — which stay in `submit_claim`.
    Those are properties of the ACT of submitting, not of the claim: a claim
    already in the system was filed in time, and re-checking grace on an
    amendment would make a `needs_info` the broker sent back on the last grace
    day unanswerable the next morning. Same trap, and the same shape of
    carve-out, as ``enforce_doctor_name`` below.

    The window is RETURNED rather than re-resolved by the caller: for a flex
    claim that repeat costs another `flex_effective_window` read, and the grace
    deadline is anchored to `period_end` off this very object. ``window`` is the
    other half of that — a caller that has ALREADY asserted the incurred date
    (submit does, because its grace deadline has to be evaluated in between)
    passes the result back in rather than paying for the resolve twice. Passing
    it therefore SKIPS the date check here; only pass a window you asserted.

    ``enforce_doctor_name`` is False for a `needs_info` resubmission, whose only
    control is attaching documents — re-checking there would permanently strand
    any pre-/post- claim recorded before the field existed. An AMENDMENT passes
    True: that form *does* carry the field, so the reason for the carve-out is
    gone.
    """
    if window is None:
        year = db.get(PolicyYear, claim.policy_year_id)
        if year is None:
            # Unreachable from `submit_claim`, which checks the year first and
            # passes its window in; the amendment paths rely on it.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Policy year not found"
            )
        window = assert_incurred_in_period(db, year, claim, employee)

    # Re-run the intake rules so a draft created before a rule (or profile)
    # change can't slip through with a missing sub-type/referral. A draft
    # holding a pre-rename sub-type label is folded onto the current one.
    claim.sub_type = normalize_sub_type(claim.sub_type)
    # Re-validate the specialist referral: a legacy draft that used the removed
    # "not applicable" escape (visit_type=None, no referral) is caught here, and
    # a follow-up draft that still has no referral_document_id gets the latest
    # letter on file linked. A draft that already names a letter keeps it (the
    # member's explicit choice at draft time).
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
    if enforce_doctor_name:
        assert_doctor_name_valid(
            claim.product_code,
            claim.sub_type,
            claim.doctor_name,
            claim_kind=claim.claim_kind,
        )
    statement = build_member_statement(db, employee)
    _apply_gp_rider_benefit_key(statement, claim, clear_rider_key=clear_rider_key)
    assert_coverage_claimable(statement, claim)
    # Every required document slot must be filled (tagged uploads); the
    # generic invoice/receipt slot accepts any attached document. Load the
    # documents once and share with the duplicate-invoice check.
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
    _assert_no_duplicate_invoice(db, claim, documents)
    return window


def submit_claim(
    db: Session,
    claim: Claim,
    employee: Employee,
    *,
    submitted_by_member_id: str | None,
) -> Claim:
    """Validate + move a claim to `submitted`. Caller commits (after audit).

    Submit = the FILING rules (year active, grace deadline) + the shared
    `validate_claim_facts` chain + the status/reference bookkeeping.
    """
    assert_transition(claim, CLAIM_STATUS_SUBMITTED)

    year = db.get(PolicyYear, claim.policy_year_id)
    if year is None or year.status != PolicyYearStatus.active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Claims can only be submitted against the active policy year.",
        )
    # Asserted HERE rather than inside the shared chain, and the order is not
    # incidental: the grace deadline below is anchored to this window's
    # `period_end`, and an out-of-period date has always been reported ahead of
    # an expired grace window. The window is handed to `validate_claim_facts`
    # so the resolve isn't paid for twice.
    window = assert_incurred_in_period(db, year, claim, employee)

    # Submission grace period: once configured, claims can only be submitted up
    # to N days after the enforced period ends. Anchored to `period_end` (the
    # flex effective end for flex claims, the policy-year end otherwise) — NOT
    # to `window.end`, which for a leaver is their own last day. How long a
    # claim may be sent in for is a property of the YEAR; how long a member was
    # covered is a separate bound with its own control
    # (`PolicyYear.leaver_access_days`). Anchoring grace on the member would
    # apply the leaver bound twice and could close their filing window before
    # their run-off even expires. None = no deadline.
    #
    # **This check is submit-only.** It does not move into
    # `validate_claim_facts` — see that function's docstring.
    if year.claim_grace_period_days is not None:
        deadline = window.period_end + timedelta(days=year.claim_grace_period_days)
        # Business date, not the UTC one: a UTC rollover closes the window at
        # 8am Singapore on its final day, so a member submitting on the last
        # morning would be refused a day early (`core/clock.py`).
        if business_today() > deadline:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"The claim submission window for this {window.period_label} closed on "
                f"{deadline.isoformat()} (period end + "
                f"{year.claim_grace_period_days} days grace).",
            )

    validate_claim_facts(
        db,
        claim,
        employee,
        # DRAFTS only — a `needs_info` resubmission cannot reach this field.
        enforce_doctor_name=claim.status == CLAIM_STATUS_DRAFT,
        window=window,
    )

    claim.status = CLAIM_STATUS_SUBMITTED
    claim.submitted_at = datetime.now(UTC)
    if submitted_by_member_id:
        claim.submitted_by_member_id = submitted_by_member_id
    # Allocate the human-quotable reference at the moment the claim becomes
    # real. `mint_reference_no` is idempotent, so a `needs_info` resubmission
    # keeps the number the member was already given — a reference that changed
    # between submissions is one neither side can look the claim up by.
    mint_reference_no(db, claim)
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
