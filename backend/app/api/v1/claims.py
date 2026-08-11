"""Broker claim review: list, inspect, decide, download documents.

Runs in the normal gated router loop (broker auth + tenant scoping via
`load_claim` / `assert_policy_year_for_user`). The AI review queue UI lands in
Phase 3 — these endpoints are its data layer and already usable directly.
"""
from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.clock import today as business_today
from app.core.deps import (
    assert_policy_year_for_user,
    load_claim,
    load_employee,
)
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.core.storage import get_storage
from app.db.session import get_db
from app.models import (
    Claim,
    ClaimAIReview,
    ClaimMessage,
    Employee,
    PolicyYear,
    StoredDocument,
)
from app.models.claim import (
    CLAIM_STATUS_AI_REVIEW_PENDING,
    CLAIM_STATUS_APPROVED,
    CLAIM_STATUS_NEEDS_INFO,
    CLAIM_STATUS_PAID,
    CLAIM_STATUS_REJECTED,
    CLAIM_STATUS_SENT_TO_INSURER,
    HOSPITAL_TYPES,
    LIVE_STATUSES,
    ORIGIN_PORTAL,
)
from app.models.claim_message import (
    AUTHOR_MEMBER,
    EVENT_APPROVED,
    EVENT_NEEDS_INFO,
    EVENT_PAID,
    EVENT_REJECTED,
)
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.claims import (
    BrokerClaimList,
    BrokerClaimOut,
    BrokerMessageIn,
    ClaimAIReviewOut,
    ClaimAIReviewSummary,
    ClaimAssessmentIn,
    ClaimBrokerAmendIn,
    ClaimCaseTypeIn,
    ClaimDecisionIn,
    ClaimMessageOut,
    ClaimPaymentIn,
    ClaimSendToInsurerIn,
    LogCaseCreateIn,
    MessagesReadOut,
    StoredDocumentOut,
)
from app.services.claim_intake import DOC_SLOT_LABELS, is_inpatient_product
from app.services.claim_messages import (
    broker_message_out,
    mark_broker_read,
    post_broker_message,
    post_system_message,
    thread_for_claim,
)
from app.services.claim_settlement import (
    AMENDMENT_COLUMNS,
    apply_settlement_amendment,
    assert_settlement_amendable,
    days_over_deadline,
    document_dates,
    insurer_days,
    record_payment,
    send_to_insurer,
    servicer_days,
)
from app.services.claim_settlement import (
    SETTLEMENT_AMENDMENTS as _SETTLEMENT_AMENDMENTS,
)
from app.services.claims import (
    BROKER_AMENDABLE_FIELDS,
    apply_claim_amendment,
    assert_amendment_reason,
    assert_claim_revision,
    assert_transition,
    attach_document,
    audit_cells,
    populate_claim_out,
    prefetch_claim_relations,
)
from app.services.claims_register import build_claims_register_workbook
from app.services.claims_review.pipeline import run_review
from app.services.log_cases import (
    case_type_or_400,
    create_log_case,
    intake_date,
    intake_field,
    set_case_type,
)
from app.services.sg_hospitals import sector_from_provider
from app.services.utilization import remaining_for_claim

router = APIRouter(prefix="/claims", tags=["claims"])
# Employee-scoped claim entry (LOG cases). A separate router so the path can be
# `/employees/{id}/…` while the handlers stay beside the rest of the claims
# surface — same split as `panel_listings.year_router`.
employee_router = APIRouter(prefix="/employees/{employee_id}", tags=["claims"])

_DECISION_STATUS = {
    "approve": CLAIM_STATUS_APPROVED,
    "reject": CLAIM_STATUS_REJECTED,
    "needs_info": CLAIM_STATUS_NEEDS_INFO,
}

# Every broker decision posts the member a notice carrying the decision note.
# Keyed off the ACTION rather than the resulting status so the mapping can't
# drift from `_DECISION_STATUS` above.
_DECISION_EVENT = {
    "approve": EVENT_APPROVED,
    "reject": EVENT_REJECTED,
    "needs_info": EVENT_NEEDS_INFO,
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


def _unread_member_messages(db: Session, claim_ids: list[str]) -> dict[str, int]:
    """Unread member replies per claim, for a whole page in ONE query. Claims
    with none are absent from the map (callers default to 0)."""
    if not claim_ids:
        return {}
    rows = db.execute(
        select(ClaimMessage.claim_id, func.count(ClaimMessage.id))
        .where(
            ClaimMessage.claim_id.in_(claim_ids),
            ClaimMessage.author_type == AUTHOR_MEMBER,
            ClaimMessage.broker_read_at.is_(None),
        )
        .group_by(ClaimMessage.claim_id)
    ).all()
    return {claim_id: count for claim_id, count in rows}


def _broker_out(
    db: Session,
    claim: Claim,
    employee: Employee | None,
    *,
    referral_docs: dict[str, StoredDocument] | None = None,
    dep_names: dict[str, str | None] | None = None,
    documents: dict[str, list[StoredDocument]] | None = None,
    unread_messages: dict[str, int] | None = None,
    doc_dates: dict[str, object] | None = None,
) -> BrokerClaimOut:
    out = BrokerClaimOut.model_validate(claim)
    # Shared filler (documents, referral letter, claimant name) — keeps the
    # broker payload in lockstep with the member's claim_to_out.
    populate_claim_out(
        db,
        claim,
        out,
        referral_docs=referral_docs,
        dep_names=dep_names,
        documents=documents,
    )
    if employee is not None:
        out.staff_id = employee.staff_id
        out.employee_name = employee.employee_name
    # Flattened out of the untyped `intake_meta` through the defensive readers,
    # so a malformed value renders as absent instead of failing the whole page.
    out.received_via = intake_field(claim, "received_via")
    out.received_on = intake_date(claim, "received_on")
    out.requested_by = intake_field(claim, "requested_by")
    review = _latest_review(db, claim.id)
    if review is not None:
        out.ai_review = ClaimAIReviewSummary.model_validate(review)
    out.unread_member_messages = (
        unread_messages
        if unread_messages is not None
        else _unread_member_messages(db, [claim.id])
    ).get(claim.id, 0)
    # SLA counters, derived from the dates. `doc_dates` is prefetched for a
    # whole page — computing it per claim would be one GROUP BY per row.
    dates = (
        doc_dates
        if doc_dates is not None
        else document_dates(db, [claim.id])
    ).get(claim.id)
    out.servicer_days = servicer_days(claim, dates)
    out.insurer_days = insurer_days(claim)
    out.days_over_deadline = days_over_deadline(claim)
    # Served, not mirrored — see `BrokerClaimOut.is_inpatient`.
    out.is_inpatient = is_inpatient_product(claim.product_code)
    out.hospital_type_derived = sector_from_provider(claim.provider_name)
    return out


@router.get("", response_model=BrokerClaimList)
def list_claims(
    policy_year_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    employee_id: str | None = Query(default=None),
    case_type: str | None = Query(default=None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimList:
    """Claims in a policy year.

    ``case_type`` defaults to None = BOTH categories. Defaulting it to `claim`
    would silently change what every existing caller receives; the queue and the
    employee-level card pass it explicitly.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    conditions = [Claim.policy_year_id == policy_year_id]
    if status_filter:
        conditions.append(Claim.status == status_filter)
    if employee_id:
        conditions.append(Claim.employee_id == employee_id)
    wanted_case_type = case_type_or_400(case_type)
    if wanted_case_type:
        conditions.append(Claim.case_type == wanted_case_type)
    total = db.scalar(select(func.count(Claim.id)).where(*conditions)) or 0
    rows = db.execute(
        select(Claim, Employee)
        .join(Employee, Claim.employee_id == Employee.id)
        .where(*conditions)
        .order_by(Claim.submitted_at.desc().nullslast(), Claim.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    referral_docs, dep_names, documents = prefetch_claim_relations(
        db, [c for c, _ in rows]
    )
    unread = _unread_member_messages(db, [c.id for c, _ in rows])
    doc_dates = document_dates(db, [c.id for c, _ in rows])
    return BrokerClaimList(
        total=total,
        offset=offset,
        limit=limit,
        items=[
            _broker_out(
                db,
                claim,
                employee,
                referral_docs=referral_docs,
                dep_names=dep_names,
                documents=documents,
                unread_messages=unread,
                doc_dates=doc_dates,
            )
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
                f'{business_today():%Y%m%d}.xlsx"'
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
    # The member may amend right up to this moment, so a decision carries the
    # revision the assessor actually read. Checked BEFORE anything is written:
    # approving a figure that changed under you is the failure this guards, and
    # it has to fail before the approval, not after it.
    assert_claim_revision(claim, body.expected_revision)

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

    # Tell the member, in the thread, carrying the broker's note. Posted BEFORE
    # the commit so a decision and its notice land in one transaction — a
    # rolled-back decision must not leave a member reading that their claim was
    # approved. Written from the claim as it stands NOW: the notice is the
    # record of what they were told, not a template re-rendered on read.
    #
    # NOT posted for a case the member never filed. They have no view of it, so
    # a notice addressed to them is at best dead data and at worst a "your claim
    # needs more information" badge on something they cannot open. The inbox
    # filters these out anyway (`member_visible_claims`); not writing them is
    # the belt to that braces.
    if claim.origin == ORIGIN_PORTAL:
        post_system_message(db, claim, _DECISION_EVENT[body.action], note=body.note)

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


@router.post("/{claim_id}/send-to-insurer", response_model=BrokerClaimOut)
def send_claim_to_insurer(
    body: ClaimSendToInsurerIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Dispatch an accepted claim to the insurer.

    No member notice is posted. The member has already been told they were
    approved; "we forwarded it" is our internal workflow, and narrating it in
    their thread would make an ordinary handoff look like a new decision.
    """
    assert_transition(claim, CLAIM_STATUS_SENT_TO_INSURER)
    before = {"status": claim.status}
    send_to_insurer(
        db,
        claim,
        user_id=user.user_id,
        sent_on=body.sent_on,
        deadline_on=body.deadline_on,
        turnaround_days=body.turnaround_days,
    )
    if body.note:
        claim.admin_remarks = body.note
    write_audit(
        db, user, "claim.sent_to_insurer", "claim", claim.id,
        before=before,
        after={
            "status": claim.status,
            "sent_to_insurer_at": (
                claim.sent_to_insurer_at.isoformat()
                if claim.sent_to_insurer_at
                else None
            ),
            "insurer_deadline_on": (
                claim.insurer_deadline_on.isoformat()
                if claim.insurer_deadline_on
                else None
            ),
        },
        employee_id=claim.employee_id,
    )
    db.commit()
    return _broker_out(db, claim, db.get(Employee, claim.employee_id))


@router.post("/{claim_id}/payment", response_model=BrokerClaimOut)
def record_claim_payment(
    body: ClaimPaymentIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Record the insurer's payment advice against a dispatched claim.

    The member IS told: "approved" and "the money is in your account" are
    different events and only the second one ends their wait.
    """
    assert_transition(claim, CLAIM_STATUS_PAID)
    before = {"status": claim.status}
    record_payment(db, claim, paid_on=body.paid_on, amount=body.amount)
    if body.note:
        claim.admin_remarks = body.note
    if claim.origin == ORIGIN_PORTAL:
        post_system_message(db, claim, EVENT_PAID, note=None)
    write_audit(
        db, user, "claim.paid", "claim", claim.id,
        before=before,
        after={
            "status": claim.status,
            "paid_on": claim.paid_on.isoformat() if claim.paid_on else None,
            "payment_amount": claim.payment_amount,
        },
        employee_id=claim.employee_id,
    )
    db.commit()
    return _broker_out(db, claim, db.get(Employee, claim.employee_id))


@router.patch("/{claim_id}/assessment", response_model=BrokerClaimOut)
def update_claim_assessment(
    body: ClaimAssessmentIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Assessor-entered detail (sector, admission dates, payroll treatment).

    A PARTIAL update driven by `model_fields_set`: these fields are edited from
    several places at different points in a claim's life, and a full-object PUT
    would let the sector form blank an admission date somebody else keyed in.
    """
    fields = body.model_fields_set
    if "hospital_type" in fields and body.hospital_type is not None:
        if body.hospital_type not in HOSPITAL_TYPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"hospital_type must be one of {sorted(HOSPITAL_TYPES)}.",
            )
    # Compare the EFFECTIVE pair — what the claim will hold after the merge —
    # not just what this request carried. A partial update that sets only the
    # discharge date is precisely how an inverted pair gets stored: the body
    # alone looks fine, and the admission date it now precedes is already on the
    # row. Which is the interaction this endpoint's partial-update design makes
    # ordinary rather than exotic.
    effective_admission = (
        body.admission_date if "admission_date" in fields else claim.admission_date
    )
    effective_discharge = (
        body.discharge_date if "discharge_date" in fields else claim.discharge_date
    )
    if (
        effective_admission is not None
        and effective_discharge is not None
        and effective_discharge < effective_admission
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "discharge_date cannot precede admission_date.",
        )
    # Settlement dates are AMENDMENTS — see `ClaimAssessmentIn`. The service
    # owns which of them this claim's state admits (nothing before dispatch; a
    # payment only on a claim recorded as paid).
    settlement = fields & _SETTLEMENT_AMENDMENTS
    assert_settlement_amendable(claim, settlement)

    # **Snapshot BEFORE anything is written.** The columns are read through
    # `AMENDMENT_COLUMNS` because `sent_to_insurer_on` is a request field whose
    # column is `sent_to_insurer_at`. Taken after the amendment, `before` was
    # read off an already-mutated claim: a broker correcting a payment from
    # 1,200.00 to 120.00 wrote an audit row saying before=120.00, after=120.00,
    # losing the only figure the row existed to preserve — and a request that
    # corrected the dispatch date alone wrote before={} / after={}, no record
    # at all. Money and dates are exactly what this trail is for.
    columns = {AMENDMENT_COLUMNS.get(f, f) for f in fields}
    before = {c: getattr(claim, c) for c in columns}

    apply_settlement_amendment(claim, body, settlement)
    for name in fields - set(AMENDMENT_COLUMNS) - settlement:
        setattr(claim, name, getattr(body, name))

    write_audit(
        db, user, "claim.assessment", "claim", claim.id,
        before=audit_cells(before),
        after=audit_cells({c: getattr(claim, c) for c in columns}),
        employee_id=claim.employee_id,
    )
    db.commit()
    return _broker_out(db, claim, db.get(Employee, claim.employee_id))


@router.patch("/{claim_id}", response_model=BrokerClaimOut)
def amend_claim(
    body: ClaimBrokerAmendIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Correct what the member stated — at any point in the claim's life.

    Distinct from `PATCH /assessment`, which records facts the BROKER owns and
    the member never stated. This one rewrites the member's own account, so it
    carries a different audit action and, once the claim is settled, demands a
    written reason.

    Runs the SAME validation chain the member's amendment and submit run: an
    assessor correcting a claim is subject to the same truth about what is
    claimable as the member who filed it, and two implementations would come to
    disagree about what a valid claim is.

    **No review supersede, and no thread notice** — unlike the member's. The
    assessor is READING the review while they correct the claim, so
    invalidating it under them is the opposite of useful; and if a broker
    changes what a member claimed, that needs a sentence a person wrote, which
    the message composer and the decision note are both already there for.
    """
    employee = db.get(Employee, claim.employee_id)
    if employee is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    assert_claim_revision(claim, body.expected_revision)
    assert_amendment_reason(claim, body.reason)

    before, after = apply_claim_amendment(
        db, claim, body, employee, allowed=BROKER_AMENDABLE_FIELDS
    )
    write_audit(
        db, user, "claim.amended", "claim", claim.id,
        before=audit_cells(before),
        after={
            **audit_cells(after),
            "revision": claim.revision,
            "reason": (body.reason or "").strip() or None,
        },
        employee_id=claim.employee_id,
    )
    db.commit()
    return _broker_out(db, claim, employee)


@employee_router.post(
    "/log-cases",
    response_model=BrokerClaimOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def create_employee_log_case(
    request: Request,
    body: LogCaseCreateIn,
    employee: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Record a LOG case for one employee, from an emailed request.

    Lands in the queue at `submitted` — no draft, and no AI review is dispatched
    (there is usually no document to extract; the assessor can still re-run one
    from the detail sheet after attaching something).
    """
    year = db.get(PolicyYear, employee.policy_year_id)
    if year is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Policy year not found")
    claim = create_log_case(db, employee, body, year, user_id=user.user_id)
    write_audit(
        db, user, "claim.log_case_created", "claim", claim.id,
        after={
            "case_type": claim.case_type,
            "product_code": claim.product_code,
            "flex_category_name": claim.flex_category_name,
            "amount": claim.amount_claimed,
            "currency": claim.currency,
            "incurred_date": claim.incurred_date.isoformat(),
            "received_via": body.received_via,
        },
        employee_id=employee.id,
    )
    db.commit()
    return _broker_out(db, claim, employee)


@router.patch("/{claim_id}/case-type", response_model=BrokerClaimOut)
def change_claim_case_type(
    body: ClaimCaseTypeIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BrokerClaimOut:
    """Reclassify a case between an ordinary claim and a LOG case.

    Keeps status, documents, messages, amounts and any AI review. A case the
    member submitted stays visible to them either way — the portal filters on
    `origin`, not on case type, precisely so that reclassifying can never
    retract someone's own record from their view.
    """
    before = {"case_type": claim.case_type, "claim_type": claim.claim_type}
    changed = set_case_type(
        claim,
        case_type=body.case_type,
        reason=body.reason,
        user_id=user.user_id,
    )
    if changed:
        write_audit(
            db, user, "claim.case_type_changed", "claim", claim.id,
            before=before,
            after={
                "case_type": claim.case_type,
                "claim_type": claim.claim_type,
                "reason": body.reason,
            },
            employee_id=claim.employee_id,
        )
        db.commit()
    employee = db.get(Employee, claim.employee_id)
    return _broker_out(db, claim, employee)


@router.post(
    "/{claim_id}/documents",
    response_model=StoredDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def upload_claim_document(
    request: Request,
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    """Attach a document to a case as the broker — the forwarded email, a
    hospital estimate, anything that arrived outside the portal.

    Unlike the member's upload this is not gated on `MEMBER_EDITABLE_STATUSES`:
    an assessor legitimately files correspondence against a case that is already
    in review. The slot tag is still validated, because the submit-time
    requirement check trusts these values.
    """
    if doc_type is not None and doc_type not in DOC_SLOT_LABELS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{doc_type}' is not a recognised document type.",
        )
    doc = await attach_document(
        db,
        client_id=claim.client_id,
        broker_firm_id=user.broker_firm_id,
        entity_type=DOC_ENTITY_CLAIM,
        entity_id=claim.id,
        file=file,
        uploaded_by_user_id=user.user_id,
        doc_type=doc_type,
    )
    write_audit(
        db, user, "claim.document_added", "claim", claim.id,
        after={
            "file_name": doc.file_name,
            "sha256": doc.sha256,
            "doc_type": doc.doc_type,
        },
        employee_id=claim.employee_id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)


@router.get("/{claim_id}/messages", response_model=list[ClaimMessageOut])
def list_claim_messages(
    claim: Claim = Depends(load_claim),
    db: Session = Depends(get_db),
) -> list[ClaimMessageOut]:
    """The claim's conversation with the member, oldest first."""
    return [broker_message_out(m) for m in thread_for_claim(db, claim.id)]


@router.post(
    "/{claim_id}/messages",
    response_model=ClaimMessageOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("30/minute")
def post_claim_message(
    request: Request,
    body: BrokerMessageIn,
    claim: Claim = Depends(load_claim),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimMessageOut:
    """Write to the member on this claim.

    **Everything posted here is member-visible** — there is no internal-note
    mode by design (see models/claim_message.py). Broker-only reasoning belongs
    in the decision note and the AI review.
    """
    msg = post_broker_message(
        db, claim, user_id=user.user_id, body=body.body, subject=body.subject
    )
    write_audit(
        db, user, "claim.message_sent", "claim", claim.id,
        after={"message_id": msg.id, "subject": msg.subject},
        employee_id=claim.employee_id,
    )
    db.commit()
    return broker_message_out(msg)


@router.post("/{claim_id}/messages/read", response_model=MessagesReadOut)
def mark_claim_messages_read(
    claim: Claim = Depends(load_claim),
    db: Session = Depends(get_db),
) -> MessagesReadOut:
    """Clear the queue's unread badge for this claim. Not audited — opening a
    thread is not an action on the record."""
    marked = mark_broker_read(db, claim_id=claim.id)
    db.commit()
    return MessagesReadOut(marked=marked)


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
