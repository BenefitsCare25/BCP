"""Member claim endpoints — every access is scoped to the caller's own
Employee row via `resolve_member_employee`; a claim id belonging to anyone
else 404s (same not-403 convention as tenant scoping)."""
from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import (
    CurrentMember,
    active_policy_year,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Claim, ClaimAIReview, StoredDocument
from app.models.claim import (
    CLAIM_KIND_FLEX,
    CLAIM_STATUS_AI_REVIEW_PENDING,
    MEMBER_EDITABLE_STATUSES,
)
from app.models.stored_document import DOC_ENTITY_CLAIM, DOC_ENTITY_REFERRAL
from app.schemas.claims import (
    ClaimCreateIn,
    ClaimList,
    ClaimOut,
    ClaimTypeOption,
    CoverageOptionsOut,
    DiagnosisOut,
    DiagnosisSearchOut,
    DocSlotOut,
    FlexClaimCategoryOption,
    FlexClaimOptions,
    HospitalOut,
    InsuredClaimOption,
    StoredDocumentOut,
)
from app.services.claim_intake import (
    ALLOWED_CURRENCIES,
    CATEGORY_INPATIENT,
    DOC_SLOT_LABELS,
    HOSPITALISATION_SLOTS_BY_SECTOR,
    SUB_TYPE_HOSPITALISATION,
    benefit_row_for_sub_type,
    claim_profile_for,
    required_doc_slots,
)
from app.services.claims import (
    attach_document,
    claim_to_out,
    create_claim,
    delete_documents,
    delete_stored_document,
    submit_claim,
)
from app.services.claims_review.pipeline import run_review
from app.services.member_statement import build_member_statement
from app.services.sg_diagnoses import search_diagnoses
from app.services.sg_hospitals import hospital_directory

router = APIRouter(
    prefix="/portal/claims",
    tags=["portal-claims"],
    dependencies=[Depends(get_current_member)],
)

# Coverage options live under /portal but belong to the claims flow — separate
# router so both register cleanly in main.py.
options_router = APIRouter(
    prefix="/portal",
    tags=["portal-claims"],
    dependencies=[Depends(get_current_member)],
)


def _own_claim(db: Session, claim_id: str, employee_id: str) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None or claim.employee_id != employee_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    return claim


@options_router.get("/coverage-options", response_model=CoverageOptionsOut)
def coverage_options(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> CoverageOptionsOut:
    """What the member may claim against — drives the claim-form pickers."""
    employee = resolve_member_employee(db, member)
    year = active_policy_year(db, member.client_id)
    statement = build_member_statement(db, employee)
    return build_coverage_options(statement, year)


def _slots(keys: list[str]) -> list[DocSlotOut]:
    return [DocSlotOut(key=k, label=DOC_SLOT_LABELS[k]) for k in keys]


def _claim_type_option(
    line, label: str, sub_type: str | None
) -> ClaimTypeOption:
    """One dropdown entry with its required-document slots. The default slots
    assume no/unlisted hospital; the Hospitalisation/Day Surgery entry also
    carries the per-sector sets so the form can switch when the member picks
    a listed hospital."""
    by_sector = (
        {k: _slots(v) for k, v in HOSPITALISATION_SLOTS_BY_SECTOR.items()}
        if sub_type == SUB_TYPE_HOSPITALISATION
        else None
    )
    return ClaimTypeOption(
        label=label,
        sub_type=sub_type,
        doc_slots=_slots(required_doc_slots(line.product_code, sub_type)),
        doc_slots_by_sector=by_sector,
    )


def build_coverage_options(statement, year) -> CoverageOptionsOut:
    """Shared by the live portal endpoint above and the broker employee-view
    preview (`portal_preview.py`) so the two can never drift."""
    insured = []
    for line in statement.coverage:
        profile = claim_profile_for(line.product_code)
        # Products settled outside the claim form (Major Medical top-up, term
        # life / personal accident / critical illness) never appear in the
        # claim-type picker.
        if not profile.member_claimable:
            continue
        base_label = (
            profile.claim_type_label or line.product_name or line.product_code
        )
        # The dropdown entries this product contributes. Inpatient products
        # expand into their sub-claim types; GP-family adds TCM/Physio riders
        # only when the member's schedule actually carries a matching row.
        if profile.category == CATEGORY_INPATIENT:
            claim_types = [
                _claim_type_option(line, s, s) for s in profile.sub_types
            ]
        else:
            claim_types = [_claim_type_option(line, base_label, None)]
            if not profile.sub_type_required:
                claim_types.extend(
                    _claim_type_option(line, s, s)
                    for s in profile.sub_types
                    if benefit_row_for_sub_type(line.benefit_schedule, s)
                )
        insured.append(
            InsuredClaimOption(
                product_code=line.product_code,
                product_name=line.product_name,
                plan_code=line.plan_code,
                annual_policy_limit=line.annual_policy_limit,
                covers_dependants=line.covers_dependants,
                covered_dependant_ids=[d.id for d in line.covered_dependants],
                sub_types=list(profile.sub_types),
                requires_referral=profile.requires_referral,
                diagnosis_group=profile.diagnosis_group,
                diagnosis_required=profile.diagnosis_required,
                category=profile.category,
                claim_types=claim_types,
            )
        )

    flex = None
    if statement.flex is not None:
        flex = FlexClaimOptions(
            currency=statement.flex.currency,
            wallet_amount=statement.flex.wallet_amount,
            flex_balance=statement.flex.flex_balance,
            categories=[
                FlexClaimCategoryOption(
                    name=c.name, sub_limit=c.sub_limit, note=c.note
                )
                for c in statement.flex.benefit_categories
                if c.claimable
            ],
            doc_slots=_slots(
                required_doc_slots(None, None, claim_kind=CLAIM_KIND_FLEX)
            ),
        )

    return CoverageOptionsOut(
        policy_year_start=year.start_date.isoformat(),
        policy_year_end=year.end_date.isoformat(),
        insured=insured,
        flex=flex,
        dependants=[
            {"id": d.id, "name": d.name, "relationship": d.relationship}
            for d in statement.dependants
        ],
        currencies=list(ALLOWED_CURRENCIES),
        hospitals=[HospitalOut(**h) for h in hospital_directory()],
    )


@options_router.get("/claim-diagnoses", response_model=DiagnosisSearchOut)
def claim_diagnoses(
    product_code: str | None = Query(default=None),
    q: str = Query(default="", max_length=128),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
) -> DiagnosisSearchOut:
    """Searchable diagnosis catalog scoped to the claim type's setting
    (GP / specialist / hospital / dental). No public SG diagnosis API exists,
    so this serves the bundled ICD-10-based catalog."""
    group = claim_profile_for(product_code).diagnosis_group if product_code else None
    hits = search_diagnoses(group, q, limit=limit)
    return DiagnosisSearchOut(
        group=group,
        items=[DiagnosisOut(label=d.label, icd10=d.icd10) for d in hits],
    )


@options_router.get("/referral-letters", response_model=list[StoredDocumentOut])
def list_my_referral_letters(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[StoredDocumentOut]:
    """The member's own referral letters (reusable across specialist claims)."""
    employee = resolve_member_employee(db, member)
    docs = db.execute(
        select(StoredDocument)
        .where(
            StoredDocument.entity_type == DOC_ENTITY_REFERRAL,
            StoredDocument.entity_id == employee.id,
        )
        .order_by(StoredDocument.created_at.desc())
    ).scalars().all()
    return [StoredDocumentOut.model_validate(d) for d in docs]


@options_router.post(
    "/referral-letters",
    response_model=StoredDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def upload_my_referral_letter(
    request: Request,
    file: UploadFile = File(...),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    employee = resolve_member_employee(db, member)
    doc = await attach_document(
        db,
        client_id=employee.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_REFERRAL,
        entity_id=employee.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
    )
    write_member_audit(
        db, member, "referral_letter.uploaded", "stored_document", doc.id,
        after={"file_name": doc.file_name, "sha256": doc.sha256},
        employee_id=employee.id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)


@options_router.delete(
    "/referral-letters/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_my_referral_letter(
    doc_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> None:
    """Remove one of the member's own referral letters. Refuses (409) while a
    claim still rides on it, so deleting can't strand a claim's
    `referral_document_id`."""
    employee = resolve_member_employee(db, member)
    doc = db.get(StoredDocument, doc_id)
    if (
        doc is None
        or doc.entity_type != DOC_ENTITY_REFERRAL
        or doc.entity_id != employee.id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referral letter not found")
    in_use = db.scalar(
        select(func.count(Claim.id)).where(Claim.referral_document_id == doc_id)
    )
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "referral_in_use",
                "message": "This referral letter is attached to a claim and "
                "can't be deleted.",
            },
        )
    delete_stored_document(db, doc)
    write_member_audit(
        db, member, "referral_letter.deleted", "stored_document", doc_id,
        employee_id=employee.id,
    )
    db.commit()


@router.get("", response_model=ClaimList)
def list_my_claims(
    status_filter: str | None = Query(default=None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimList:
    employee = resolve_member_employee(db, member)
    conditions = [
        Claim.employee_id == employee.id,
        Claim.policy_year_id == employee.policy_year_id,
    ]
    if status_filter:
        conditions.append(Claim.status == status_filter)
    total = db.scalar(select(func.count(Claim.id)).where(*conditions)) or 0
    rows = db.execute(
        select(Claim)
        .where(*conditions)
        .order_by(Claim.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return ClaimList(
        total=total,
        offset=offset,
        limit=limit,
        items=[claim_to_out(db, c) for c in rows],
    )


@router.post("", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_my_claim(
    request: Request,
    body: ClaimCreateIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    employee = resolve_member_employee(db, member)
    claim = create_claim(
        db, employee, body, submitted_by_member_id=member.member_account_id
    )
    write_member_audit(
        db, member, "claim.drafted", "claim", claim.id,
        after={"claim_kind": claim.claim_kind, "amount": claim.amount_claimed},
        employee_id=employee.id,
    )
    db.commit()
    return claim_to_out(db, claim)


@router.get("/{claim_id}", response_model=ClaimOut)
def get_my_claim(
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    employee = resolve_member_employee(db, member)
    return claim_to_out(db, _own_claim(db, claim_id, employee.id))


@router.post("/{claim_id}/documents", response_model=StoredDocumentOut)
@limiter.limit("20/minute")
async def upload_my_claim_document(
    request: Request,
    claim_id: str,
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    if claim.status not in MEMBER_EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Documents can only be added while the claim is editable.",
        )
    # The slot tag must be a known slot key — the submit-time requirement
    # check trusts these values.
    if doc_type is not None and doc_type not in DOC_SLOT_LABELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{doc_type}' is not a recognised document type.",
        )
    doc = await attach_document(
        db,
        client_id=claim.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_CLAIM,
        entity_id=claim.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
        doc_type=doc_type,
    )
    write_member_audit(
        db, member, "claim.document_added", "claim", claim.id,
        after={"file_name": doc.file_name, "sha256": doc.sha256, "doc_type": doc.doc_type},
        employee_id=employee.id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)


@router.delete("/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_draft_claim(
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> None:
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    if claim.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a draft claim can be deleted."
        )
    delete_documents(db, DOC_ENTITY_CLAIM, claim.id)
    write_member_audit(
        db, member, "claim.deleted", "claim", claim.id, employee_id=employee.id
    )
    db.delete(claim)
    db.commit()


@router.post("/{claim_id}/submit", response_model=ClaimOut)
@limiter.limit("10/minute")
def submit_my_claim(
    request: Request,
    claim_id: str,
    background_tasks: BackgroundTasks,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    submit_claim(
        db, claim, employee, submitted_by_member_id=member.member_account_id
    )
    # Queue the AI review — it runs after the response; a pipeline fault
    # degrades the claim back to plain `submitted` (manual review), so the
    # member is never blocked.
    claim.status = CLAIM_STATUS_AI_REVIEW_PENDING
    review = ClaimAIReview(client_id=claim.client_id, claim_id=claim.id)
    db.add(review)
    db.flush()
    write_member_audit(
        db, member, "claim.submitted", "claim", claim.id,
        after={
            "claim_kind": claim.claim_kind,
            "product_code": claim.product_code,
            "flex_category_name": claim.flex_category_name,
            "amount": claim.amount_claimed,
            "currency": claim.currency,
        },
        employee_id=employee.id,
    )
    db.commit()
    background_tasks.add_task(run_review, claim.id, review.id, member.broker_firm_id)
    return claim_to_out(db, claim)
