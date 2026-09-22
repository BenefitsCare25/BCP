"""Company HR delegated claims. HR remains the actor, never a member principal."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.clock import today as business_today
from app.core.hr_auth import get_current_hr_user
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import (
    Claim,
    Client,
    Employee,
    PolicyYear,
    StoredDocument,
    User,
    UserClientAccess,
)
from app.models.claim import (
    AMENDED_BY_HR,
    MEMBER_EDITABLE_STATUSES,
    MEMBER_SUBMITTABLE_STATUSES,
    ORIGIN_HR,
)
from app.models.claim_message import EVENT_SUBMITTED
from app.models.stored_document import DOC_ENTITY_CLAIM, DOC_ENTITY_REFERRAL
from app.schemas.claims import ClaimCreateIn, CoverageOptionsOut, FxQuoteOut
from app.services.claim_document_setups import setup_for_claim
from app.services.claim_fx import build_quote
from app.services.claim_integrity import (
    is_replayed_claim_command,
    lock_claim_command_key,
    record_claim_command,
    replayed_claim_for_command,
)
from app.services.claim_messages import post_system_message
from app.services.claims import (
    attach_document,
    claim_documents,
    create_claim,
    lock_claim_for_mutation,
    stamp_document_amendment,
    submit_claim,
)
from app.services.claims_review.queue import enqueue_amended_claim_review, enqueue_claim_review
from app.services.member_access import Capability, access_of, refusal
from app.services.member_statement import build_member_statement

router = APIRouter(prefix="/hr/claims", tags=["hr-claims"])


def delegated_hr(
    user: CurrentUser = Depends(get_current_hr_user),
    db: Session = Depends(get_db),
) -> CurrentUser:
    # Re-check the live grant: a still-valid token must not outlive revocation.
    client = db.get(Client, user.client_id)
    grant = db.scalar(
        select(UserClientAccess.id).where(
            UserClientAccess.user_id == user.user_id,
            UserClientAccess.client_id == user.client_id,
        )
    )
    if (
        user.role not in {"client_admin", "client_hr"}
        or not grant
        or client is None
        or not user.broker_firm_id
        or client.broker_firm_id != user.broker_firm_id
    ):
        raise HTTPException(403, "HR company access is not available.")
    return user


def _employee(db: Session, user: CurrentUser, employee_id: str, capability: Capability) -> Employee:
    employee = db.get(Employee, employee_id)
    year = db.get(PolicyYear, employee.policy_year_id) if employee else None
    if (
        employee is None
        or employee.client_id != user.client_id
        or year is None
        or year.client_id != user.client_id
        or year.status not in {"active", "archived"}
    ):
        raise HTTPException(404, "Employee not found")
    denied = refusal(access_of(db, employee, year), capability)
    if denied:
        raise HTTPException(403, denied)
    return employee


def _scope(user: CurrentUser) -> list[Any]:
    conditions = [
        Claim.client_id == user.client_id,
        Claim.intake_meta["submission_channel"].as_string() == "hr",
    ]
    if user.role != "client_admin":
        conditions.append(Claim.created_by_user_id == user.user_id)
    return conditions


def _claim(db: Session, user: CurrentUser, claim_id: str) -> Claim:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id, *_scope(user)))
    if claim is None:
        raise HTTPException(404, "Delegated claim not found")
    return claim


def _assert_evidence_mutable(claim: Claim) -> None:
    if claim.status not in MEMBER_EDITABLE_STATUSES:
        raise HTTPException(403, "Evidence is retained after a decision.")


def _out(db: Session, claim: Claim) -> dict[str, Any]:
    # Explicit allowlist: no medical narrative, broker notes, AI verdict or
    # member message history. HR sees only work submitted through this flow.
    employee = db.get(Employee, claim.employee_id)
    return {
        "id": claim.id,
        "employee_id": claim.employee_id,
        "employee_name": employee.employee_name if employee else None,
        "policy_year_id": claim.policy_year_id,
        "claim_ref": claim.reference_no,
        "claim_kind": claim.claim_kind,
        "claim_type": claim.claim_type,
        "provider_name": claim.provider_name,
        "invoice_number": claim.invoice_number,
        "status": claim.status,
        "incurred_date": claim.incurred_date,
        "amount_claimed": claim.amount_claimed,
        "currency": claim.currency,
        "created_by_user_id": claim.created_by_user_id,
        "submitted_by_name": _meta_text(claim, "delegated_by_name"),
        "submitted_by_email": _meta_text(claim, "delegated_by_email"),
        "submission_channel": "hr",
        "can_add_evidence": claim.status in MEMBER_EDITABLE_STATUSES,
        "can_submit": claim.status in MEMBER_SUBMITTABLE_STATUSES,
        "created_at": claim.created_at,
        "submitted_at": claim.submitted_at,
        "doc_slots": [
            {"key": d.key, "label": d.display, "instructions": d.instructions}
            for d in setup_for_claim(db, claim).documents
        ],
        "documents": [
            {"id": d.id, "file_name": d.file_name, "doc_type": d.doc_type}
            for d in claim_documents(db, claim)
        ],
    }


def _meta_text(claim: Claim, key: str) -> str | None:
    value = (claim.intake_meta or {}).get(key)
    return value if isinstance(value, str) and value.strip() else None


class DelegatedClaimIn(ClaimCreateIn):
    model_config = ConfigDict(extra="forbid")
    employee_id: str


@router.get("/employees")
def employees(
    q: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [
        Employee.client_id == user.client_id,
        PolicyYear.client_id == user.client_id,
        PolicyYear.status.in_(["active", "archived"]),
    ]
    if q.strip():
        conditions.append(
            or_(
                Employee.employee_name.icontains(q.strip(), autoescape=True),
                Employee.staff_id.icontains(q.strip(), autoescape=True),
            )
        )
    query = (
        select(Employee, PolicyYear)
        .join(PolicyYear, Employee.policy_year_id == PolicyYear.id)
        .where(*conditions)
    )
    rows = db.execute(
        query.order_by(PolicyYear.start_date.desc(), Employee.employee_name, Employee.id)
        .offset(offset)
        .limit(50)
    ).all()
    return {
        "items": [
            {
                "id": e.id,
                "name": e.employee_name,
                "staff_id": e.staff_id,
                "period": f"{y.start_date} - {y.end_date}",
            }
            for e, y in rows
        ],
        "total": db.scalar(select(func.count()).select_from(query.subquery())) or 0,
    }


@router.get("/employees/{employee_id}/options", response_model=CoverageOptionsOut)
def options(
    employee_id: str,
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> CoverageOptionsOut:
    from app.api.v1.portal_claims import build_coverage_options

    employee = _employee(db, user, employee_id, Capability.CLAIM)
    year = db.get(PolicyYear, employee.policy_year_id)
    if year is None:
        raise HTTPException(404, "Policy year not found")
    return build_coverage_options(db, build_member_statement(db, employee), employee, year)


@router.post("/employees/{employee_id}/referrals", status_code=201)
@limiter.limit("20/minute")
async def referral(
    request: Request,
    employee_id: str,
    file: UploadFile = File(...),
    issued_on: date | None = Form(default=None),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    employee = _employee(db, user, employee_id, Capability.CLAIM)
    if issued_on and issued_on > business_today():
        raise HTTPException(422, "A referral letter cannot be dated in the future.")
    doc = await attach_document(
        db,
        client_id=employee.client_id,
        broker_firm_id=user.broker_firm_id,
        entity_type=DOC_ENTITY_REFERRAL,
        entity_id=employee.id,
        file=file,
        uploaded_by_user_id=user.user_id,
        issued_on=issued_on,
    )
    write_audit(
        db,
        user,
        "hr.referral_uploaded",
        "stored_document",
        doc.id,
        employee_id=employee.id,
        request=request,
    )
    db.commit()
    return {"id": doc.id, "file_name": doc.file_name}


@router.get("")
def list_claims(
    q: str = Query(default="", max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    conditions = [*_scope(user), Employee.client_id == user.client_id]
    term = q.strip()
    if term:
        conditions.append(
            or_(
                Employee.employee_name.icontains(term, autoescape=True),
                Claim.reference_no.icontains(term, autoescape=True),
                Claim.claim_type.icontains(term, autoescape=True),
                Claim.provider_name.icontains(term, autoescape=True),
                Claim.invoice_number.icontains(term, autoescape=True),
            )
        )
    query = select(Claim).join(Employee, Claim.employee_id == Employee.id).where(*conditions)
    rows = db.scalars(
        query.order_by(Claim.created_at.desc(), Claim.id).offset(offset).limit(limit)
    ).all()
    return {
        "items": [_out(db, c) for c in rows],
        "total": db.scalar(select(func.count()).select_from(query.subquery())) or 0,
    }


@router.get("/fx-quote", response_model=FxQuoteOut)
@limiter.limit("60/minute")
def hr_fx_quote(
    request: Request,
    currency: str = Query(min_length=3, max_length=8),
    amount: float = Query(gt=0, le=1_000_000),
    on: date = Query(description="The date on the receipt."),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> FxQuoteOut:
    """Preview a delegated claim conversion before HR saves the draft."""
    del user
    out = build_quote(db, currency=currency, amount=amount, on=on)
    db.commit()
    return out


@router.post("", status_code=201)
@limiter.limit("10/minute")
def draft(
    request: Request,
    body: DelegatedClaimIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    employee = _employee(db, user, body.employee_id, Capability.CLAIM)
    if body.intake_id is not None:
        raise HTTPException(422, "Member intake sessions cannot be used by HR.")
    if body.related_claim_id:
        _claim(db, user, body.related_claim_id)
    if (
        body.visit_type == "follow_up"
        and body.related_claim_id is None
        and body.referral_document_id is None
    ):
        raise HTTPException(
            422,
            "Attach the referral for this specialist follow-up or select its prior visit.",
        )
    if body.referral_document_id:
        doc = db.get(StoredDocument, body.referral_document_id)
        if (
            doc is None
            or doc.client_id != user.client_id
            or doc.entity_id != employee.id
            or doc.entity_type != DOC_ENTITY_REFERRAL
            or doc.uploaded_by_user_id != user.user_id
        ):
            raise HTTPException(404, "Referral not found")
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    action = f"hr:create:{user.user_id}"
    lock_claim_command_key(db, employee.client_id, idempotency_key)
    replay = replayed_claim_for_command(
        db,
        client_id=employee.client_id,
        employee_id=employee.id,
        action=action,
        idempotency_key=idempotency_key,
        request_hash=fingerprint,
    )
    if replay:
        return _out(db, _claim(db, user, replay.id))
    claim = create_claim(db, employee, body, submitted_by_member_id=None)
    actor = db.get(User, user.user_id)
    actor_name = (actor.display_name or "").strip() if actor else ""
    claim.created_by_user_id = user.user_id
    claim.origin = ORIGIN_HR
    claim.intake_meta = {
        **(claim.intake_meta or {}),
        "submission_channel": "hr",
        "delegated_by_user_id": user.user_id,
        "delegated_by_name": actor_name or None,
        "delegated_by_email": user.email,
    }
    write_audit(
        db, user, "hr.claim_drafted", "claim", claim.id, employee_id=employee.id, request=request
    )
    record_claim_command(db, claim, action, idempotency_key, fingerprint)
    db.commit()
    return _out(db, claim)


@router.get("/{claim_id}")
def detail(
    claim_id: str,
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return _out(db, _claim(db, user, claim_id))


@router.post("/{claim_id}/documents", status_code=201)
@limiter.limit("20/minute")
async def upload(
    request: Request,
    claim_id: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    claim = lock_claim_for_mutation(db, _claim(db, user, claim_id))
    _employee(
        db,
        user,
        claim.employee_id,
        Capability.CLAIM if claim.status == "draft" else Capability.RESPOND,
    )
    _assert_evidence_mutable(claim)
    if doc_type not in {d.key for d in setup_for_claim(db, claim).documents}:
        raise HTTPException(422, "Unknown evidence type")
    await attach_document(
        db,
        client_id=claim.client_id,
        broker_firm_id=user.broker_firm_id,
        entity_type=DOC_ENTITY_CLAIM,
        entity_id=claim.id,
        file=file,
        uploaded_by_user_id=user.user_id,
        doc_type=doc_type,
    )
    stamp_document_amendment(db, claim, actor=AMENDED_BY_HR)
    enqueue_amended_claim_review(db, claim, user.broker_firm_id)
    write_audit(
        db,
        user,
        "hr.claim_evidence_added",
        "claim",
        claim.id,
        employee_id=claim.employee_id,
        request=request,
    )
    db.commit()
    return _out(db, claim)


@router.post("/{claim_id}/submit")
@limiter.limit("10/minute")
def submit(
    request: Request,
    claim_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
    user: CurrentUser = Depends(delegated_hr),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    claim = lock_claim_for_mutation(db, _claim(db, user, claim_id))
    action = f"hr:submit:{user.user_id}"
    if is_replayed_claim_command(db, claim, action, idempotency_key):
        return _out(db, claim)
    employee = _employee(
        db,
        user,
        claim.employee_id,
        Capability.CLAIM if claim.status == "draft" else Capability.RESPOND,
    )
    submit_claim(db, claim, employee, submitted_by_member_id=None)
    if user.broker_firm_id is None:
        raise HTTPException(403, "HR company access is not available.")
    enqueue_claim_review(db, claim, user.broker_firm_id, supersede=True)
    post_system_message(db, claim, EVENT_SUBMITTED)
    write_audit(
        db, user, "hr.claim_submitted", "claim", claim.id, employee_id=employee.id, request=request
    )
    record_claim_command(db, claim, action, idempotency_key)
    db.commit()
    return _out(db, claim)
