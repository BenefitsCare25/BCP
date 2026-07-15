"""Member claim endpoints — every access is scoped to the caller's own
Employee row via `resolve_member_employee`; a claim id belonging to anyone
else 404s (same not-403 convention as tenant scoping)."""
from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
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
from app.models import Claim, ClaimAIReview
from app.models.claim import CLAIM_STATUS_AI_REVIEW_PENDING, MEMBER_EDITABLE_STATUSES
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.claims import (
    ClaimCreateIn,
    ClaimList,
    ClaimOut,
    CoverageOptionsOut,
    FlexClaimCategoryOption,
    FlexClaimOptions,
    InsuredClaimOption,
    StoredDocumentOut,
)
from app.services.claims import (
    attach_document,
    claim_to_out,
    create_claim,
    delete_documents,
    submit_claim,
)
from app.services.claims_review.pipeline import run_review
from app.services.member_statement import build_member_statement

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


def build_coverage_options(statement, year) -> CoverageOptionsOut:
    """Shared by the live portal endpoint above and the broker employee-view
    preview (`portal_preview.py`) so the two can never drift."""
    insured = []
    for line in statement.coverage:
        items = (line.benefit_schedule or {}).get("items") or []
        insured.append(
            InsuredClaimOption(
                product_code=line.product_code,
                product_name=line.product_name,
                plan_code=line.plan_code,
                annual_policy_limit=line.annual_policy_limit,
                benefit_items=[
                    str(i.get("name")) for i in items
                    if isinstance(i, dict) and i.get("name")
                ],
                covers_dependants=line.covers_dependants,
                covered_dependant_ids=[d.id for d in line.covered_dependants],
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
    )


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
    doc = await attach_document(
        db,
        client_id=claim.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_CLAIM,
        entity_id=claim.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
    )
    write_member_audit(
        db, member, "claim.document_added", "claim", claim.id,
        after={"file_name": doc.file_name, "sha256": doc.sha256},
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
