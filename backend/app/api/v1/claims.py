"""Broker claim review: list, inspect, decide, download documents.

Runs in the normal gated router loop (broker auth + tenant scoping via
`load_claim` / `assert_policy_year_for_user`). The AI review queue UI lands in
Phase 3 — these endpoints are its data layer and already usable directly.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from io import BytesIO

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user, load_claim
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.core.storage import get_storage
from app.db.session import get_db
from app.models import Claim, ClaimAIReview, Employee, StoredDocument
from app.models.claim import (
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_APPROVED,
    CLAIM_STATUS_NEEDS_INFO,
    CLAIM_STATUS_REJECTED,
    LIVE_STATUSES,
)
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.claims import (
    BrokerClaimList,
    BrokerClaimOut,
    ClaimAIReviewOut,
    ClaimAIReviewSummary,
    ClaimDecisionIn,
)
from app.services.claims import (
    assert_transition,
    populate_claim_out,
    prefetch_claim_relations,
)
from app.services.claims_register import build_claims_register_workbook
from app.services.claims_review.pipeline import run_review
from app.services.utilization import remaining_for_claim

router = APIRouter(prefix="/claims", tags=["claims"])

_DECISION_STATUS = {
    "approve": CLAIM_STATUS_APPROVED,
    "reject": CLAIM_STATUS_REJECTED,
    "needs_info": CLAIM_STATUS_NEEDS_INFO,
}


def _latest_review(db: Session, claim_id: str) -> ClaimAIReview | None:
    return db.execute(
        select(ClaimAIReview)
        .where(
            ClaimAIReview.claim_id == claim_id,
            ClaimAIReview.superseded.is_(False),
        )
        .order_by(ClaimAIReview.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _broker_out(
    db: Session,
    claim: Claim,
    employee: Employee | None,
    *,
    referral_docs: dict[str, StoredDocument] | None = None,
    dep_names: dict[str, str | None] | None = None,
) -> BrokerClaimOut:
    out = BrokerClaimOut.model_validate(claim)
    # Shared filler (documents, referral letter, claimant name) — keeps the
    # broker payload in lockstep with the member's claim_to_out.
    populate_claim_out(
        db, claim, out, referral_docs=referral_docs, dep_names=dep_names
    )
    if employee is not None:
        out.staff_id = employee.staff_id
        out.employee_name = employee.employee_name
    review = _latest_review(db, claim.id)
    if review is not None:
        out.ai_review = ClaimAIReviewSummary.model_validate(review)
    return out


@router.get("", response_model=BrokerClaimList)
def list_claims(
    policy_year_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    employee_id: str | None = Query(default=None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimList:
    assert_policy_year_for_user(policy_year_id, user, db)
    conditions = [Claim.policy_year_id == policy_year_id]
    if status_filter:
        conditions.append(Claim.status == status_filter)
    if employee_id:
        conditions.append(Claim.employee_id == employee_id)
    total = db.scalar(select(func.count(Claim.id)).where(*conditions)) or 0
    rows = db.execute(
        select(Claim, Employee)
        .join(Employee, Claim.employee_id == Employee.id)
        .where(*conditions)
        .order_by(Claim.submitted_at.desc().nullslast(), Claim.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    referral_docs, dep_names = prefetch_claim_relations(db, [c for c, _ in rows])
    return BrokerClaimList(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            _broker_out(db, claim, employee, referral_docs=referral_docs, dep_names=dep_names)
            for claim, employee in rows
        ],
    )


@router.get("/register")
@limiter.limit("20/minute")
def download_claims_register(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Claims register (.xlsx) — every claim in the policy year, one per row."""
    py = assert_policy_year_for_user(policy_year_id, user, db)
    wb = build_claims_register_workbook(db, py)
    write_audit(
        db, user, action="export", entity_type="claims_register",
        entity_id=policy_year_id, after={"report": "claims-register"},
    )
    db.commit()
    buf = BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                'attachment; filename="claims-register-'
                f'{date.today():%Y%m%d}.xlsx"'
            )
        },
    )


@router.get("/{claim_id}", response_model=BrokerClaimOut)
def get_claim(
    claim: Claim = Depends(load_claim),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    employee = db.get(Employee, claim.employee_id)
    out = _broker_out(db, claim, employee)
    # The remaining limit for this claim's bucket, shown ahead of the decision
    # (the approve endpoint still enforces it). Detail-only — utilization is
    # computed-on-read and too heavy for the list.
    if employee is not None and claim.status in LIVE_STATUSES:
        out.remaining_limit = remaining_for_claim(db, claim, employee)
    return out


@router.post("/{claim_id}/decision", response_model=BrokerClaimOut)
def decide_claim(
    body: ClaimDecisionIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    new_status = _DECISION_STATUS[body.action]
    assert_transition(claim, new_status)

    before = {"status": claim.status, "amount_approved": claim.amount_approved}
    if body.action == "approve":
        approving = (
            body.approved_amount
            if body.approved_amount is not None
            else (claim.amount_converted or claim.amount_claimed)
        )
        # Approving beyond the bucket's remaining limit needs an explicit
        # acknowledgement — the utilization math already excludes this claim
        # (only *approved* claims count against the limit).
        if not body.acknowledge:
            employee = db.get(Employee, claim.employee_id)
            remaining = (
                remaining_for_claim(db, claim, employee)
                if employee is not None
                else None
            )
            if remaining is not None and approving > remaining:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={
                        "code": "limit_exceeded",
                        "message": (
                            f"Approving {approving:.2f} exceeds the remaining "
                            f"limit of {remaining:.2f} for this coverage. "
                            "Resend with acknowledge=true to approve anyway."
                        ),
                        "remaining": remaining,
                        "approving": approving,
                    },
                )
        claim.amount_approved = approving
        claim.decided_at = datetime.now(UTC)
        claim.decided_by = user.user_id
    elif body.action == "reject":
        claim.amount_approved = None
        claim.decided_at = datetime.now(UTC)
        claim.decided_by = user.user_id
    else:  # needs_info reopens the claim for the member — not a decision
        claim.decided_at = None
        claim.decided_by = None
    claim.status = new_status
    claim.decision_notes = body.note

    write_audit(
        db, user, f"claim.{body.action}", "claim", claim.id,
        before=before,
        after={
            "status": claim.status,
            "amount_approved": claim.amount_approved,
            "note": body.note,
        },
        employee_id=claim.employee_id,
    )
    db.commit()
    employee = db.get(Employee, claim.employee_id)
    return _broker_out(db, claim, employee)


@router.get("/{claim_id}/review", response_model=ClaimAIReviewOut)
def get_claim_review(
    claim: Claim = Depends(load_claim),
    db: Session = Depends(get_db),
) -> ClaimAIReviewOut:
    """Latest (non-superseded) AI review of the claim."""
    review = _latest_review(db, claim.id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No AI review for this claim")
    return ClaimAIReviewOut.model_validate(review)


@router.post("/{claim_id}/rerun-review", response_model=BrokerClaimOut)
@limiter.limit("10/minute")
def rerun_claim_review(
    request: Request,
    background_tasks: BackgroundTasks,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Supersede the current AI review and re-run the pipeline."""
    assert_transition(claim, CLAIM_STATUS_AI_REVIEW_PENDING)

    for old in db.execute(
        select(ClaimAIReview).where(
            ClaimAIReview.claim_id == claim.id,
            ClaimAIReview.superseded.is_(False),
        )
    ).scalars():
        old.superseded = True

    review = ClaimAIReview(client_id=claim.client_id, claim_id=claim.id)
    db.add(review)
    db.flush()
    before = {"status": claim.status}
    claim.status = CLAIM_STATUS_AI_REVIEW_PENDING
    write_audit(
        db, user, "claim.rerun_review", "claim", claim.id,
        before=before,
        after={"status": claim.status, "review_id": review.id},
        employee_id=claim.employee_id,
    )
    db.commit()
    background_tasks.add_task(run_review, claim.id, review.id, user.broker_firm_id)
    employee = db.get(Employee, claim.employee_id)
    return _broker_out(db, claim, employee)


@router.get("/{claim_id}/documents/{doc_id}/download")
def download_claim_document(
    doc_id: str,
    claim: Claim = Depends(load_claim),
    db: Session = Depends(get_db),
) -> Response:
    doc = db.get(StoredDocument, doc_id)
    # A claim's own attachments, plus the member-level referral letter the
    # claim rides on (entity_type="referral") — nothing else.
    is_claim_doc = (
        doc is not None
        and doc.entity_type == DOC_ENTITY_CLAIM
        and doc.entity_id == claim.id
    )
    is_referral = doc is not None and doc.id == claim.referral_document_id
    if doc is None or not (is_claim_doc or is_referral):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = get_storage().read(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Document bytes are no longer available"
        ) from None
    safe_name = doc.file_name.replace('"', "")
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )
