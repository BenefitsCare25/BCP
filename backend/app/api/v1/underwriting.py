"""Underwriting queue — insurer-grouped reviews + per-product decisions.

OPERATIONAL surface (not activation-locked): reviews exist precisely because a
live year's members exceed a product's Non-Evidence Limit (dollar FCL or age
gate). One review per (life, insurer) carries the broker↔insurer workflow
status + requirements; its case lines carry per-product decisions. Amounts
feed the insurer listings' "Sum Insured Pending U/W" / "Last Accepted Sum
Insured".
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import _deny_cross_tenant, assert_policy_year_for_user, user_owns
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import (
    Dependant,
    Employee,
    Product,
    UnderwritingCase,
    UnderwritingReview,
)
from app.models.underwriting_case import (
    DECIDED_UW_STATUSES,
    OPEN_REVIEW_STATUSES,
    VALID_REVIEW_STATUSES,
    VALID_UW_STATUSES,
    UnderwritingStatus,
    normalize_uw_status,
)
from app.services.flex_membership import classify_relationship
from app.services.roster_attributes import (
    EMPLOYEE_ID_KEYS,
    NAME_KEYS,
    REL_KEYS,
    first_value,
)
from app.services.underwriting import (
    adopt_orphan_cases,
    case_amounts,
    refresh_underwriting_cases,
)

router = APIRouter(tags=["underwriting"])

# Decisions where the insurer said yes — the accepted figure defaults to the
# full requested SI rather than the guaranteed floor.
_APPROVED_STATUSES = frozenset(
    {
        UnderwritingStatus.approved_standard,
        UnderwritingStatus.approved_substandard,
    }
)


class UnderwritingCaseOut(BaseModel):
    id: str
    product_id: str
    product_code: str
    product_name: str
    # Requested = the life's eligible SI; guaranteed = auto-covered while the
    # insurer decides; pending = requested - in force while undecided.
    requested_si: float
    guaranteed_si: float
    pending_si: float
    accepted_si: float
    status: str  # decision vocabulary (pending / approved_standard / …)
    decided_on: date | None
    remarks: str | None


class UnderwritingReviewOut(BaseModel):
    id: str
    insurer: str
    subject_type: str  # "employee" | "dependant"
    subject_name: str | None
    relationship: str  # "Self" for the member; classified role for dependants
    staff_id: str | None
    identification_no: str | None
    status: str
    requirements: str | None
    cases: list[UnderwritingCaseOut]


class UnderwritingQueueOut(BaseModel):
    total: int
    open: int
    pending_amount: float
    items: list[UnderwritingReviewOut]


class UnderwritingReviewIn(BaseModel):
    status: str | None = None
    requirements: str | None = Field(default=None, max_length=2000)


class UnderwritingDecisionIn(BaseModel):
    status: str
    accepted_si: float | None = Field(default=None, ge=0)
    guaranteed_si: float | None = Field(default=None, ge=0)
    decided_on: date | None = None
    remarks: str | None = Field(default=None, max_length=1024)


class RefreshOut(BaseModel):
    opened: int
    updated: int
    removed: int
    open_cases: int


def _case_out(case: UnderwritingCase, products: dict[str, Product]) -> UnderwritingCaseOut:
    decision = normalize_uw_status(case.status)
    guaranteed, pending, accepted = case_amounts(case)
    product = products.get(case.product_id)
    return UnderwritingCaseOut(
        id=case.id,
        product_id=case.product_id,
        product_code=product.code if product else "?",
        product_name=product.display_name if product else "Unknown product",
        requested_si=case.eligible_si,
        guaranteed_si=guaranteed,
        pending_si=pending,
        accepted_si=accepted,
        status=decision,
        decided_on=case.decided_on,
        remarks=case.remarks,
    )


def _review_out(
    review: UnderwritingReview,
    lines: list[UnderwritingCase],
    products: dict[str, Product],
    employees: dict[str, Employee],
    dependants: dict[str, Dependant],
) -> UnderwritingReviewOut:
    if review.employee_id:
        emp = employees.get(review.employee_id)
        subject_type = "employee"
        subject_name = emp.employee_name if emp else None
        relationship = "Self"
        staff_id = emp.staff_id if emp else None
        ident = (
            first_value(emp.attribute_values or {}, EMPLOYEE_ID_KEYS) if emp else None
        )
    else:
        dep = dependants.get(review.dependant_id or "")
        emp = employees.get(dep.employee_id) if dep and dep.employee_id else None
        subject_type = "dependant"
        attrs = (dep.attribute_values or {}) if dep else {}
        subject_name = first_value(attrs, NAME_KEYS)
        role = classify_relationship(first_value(attrs, REL_KEYS))
        relationship = (role or "dependant").capitalize()
        staff_id = emp.staff_id if emp else None
        ident = first_value(attrs, ("dependant_id_no", "id_no", "nric", "fin"))
    def _line_order(c: UnderwritingCase) -> str:
        product = products.get(c.product_id)
        return product.code if product else "?"

    lines_sorted = sorted(lines, key=_line_order)
    return UnderwritingReviewOut(
        id=review.id,
        insurer=review.insurer,
        subject_type=subject_type,
        subject_name=subject_name,
        relationship=relationship,
        staff_id=staff_id,
        identification_no=ident,
        status=review.status,
        requirements=review.requirements,
        cases=[_case_out(c, products) for c in lines_sorted],
    )


def _queue(db: Session, policy_year_id: str) -> UnderwritingQueueOut:
    reviews = list(
        db.execute(
            select(UnderwritingReview)
            .where(UnderwritingReview.policy_year_id == policy_year_id)
            .order_by(UnderwritingReview.created_at)
        ).scalars().all()
    )
    cases = list(
        db.execute(
            select(UnderwritingCase)
            .where(UnderwritingCase.policy_year_id == policy_year_id)
            .order_by(UnderwritingCase.created_at)
        ).scalars().all()
    )
    lines_by_review: dict[str, list[UnderwritingCase]] = {}
    for c in cases:
        if c.review_id:
            lines_by_review.setdefault(c.review_id, []).append(c)

    product_ids = {c.product_id for c in cases}
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(Product.id.in_(product_ids))
        ).scalars().all()
    } if product_ids else {}
    emp_ids = {r.employee_id for r in reviews if r.employee_id}
    dep_ids = {r.dependant_id for r in reviews if r.dependant_id}
    dependants = {
        d.id: d
        for d in db.execute(
            select(Dependant).where(Dependant.id.in_(dep_ids))
        ).scalars().all()
    } if dep_ids else {}
    emp_ids |= {d.employee_id for d in dependants.values() if d.employee_id}
    employees = {
        e.id: e
        for e in db.execute(
            select(Employee).where(Employee.id.in_(emp_ids))
        ).scalars().all()
    } if emp_ids else {}

    items = [
        _review_out(r, lines_by_review.get(r.id, []), products, employees, dependants)
        for r in reviews
    ]
    # Open reviews first, then insurer / member name for a stable scan order.
    items.sort(
        key=lambda r: (
            r.status not in OPEN_REVIEW_STATUSES,
            r.insurer.lower(),
            (r.subject_name or "").lower(),
        )
    )
    return UnderwritingQueueOut(
        total=len(items),
        open=sum(1 for i in items if i.status in OPEN_REVIEW_STATUSES),
        pending_amount=sum(c.pending_si for i in items for c in i.cases),
        items=items,
    )


@router.get(
    "/policy-years/{policy_year_id}/underwriting/cases",
    response_model=UnderwritingQueueOut,
)
def list_underwriting_cases(
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnderwritingQueueOut:
    assert_policy_year_for_user(policy_year_id, user, db)
    # Lazily adopt pre-review-model rows (same shape as the portal enrollment
    # GET materializing a missing Enrollment). Without it, cases written before
    # the insurer-grouped model stay invisible — and undecidable — until some
    # unrelated action happens to run a full sync. No-op once done.
    if adopt_orphan_cases(db, policy_year_id):
        db.commit()
    return _queue(db, policy_year_id)


@router.post(
    "/policy-years/{policy_year_id}/underwriting/refresh",
    response_model=RefreshOut,
)
@limiter.limit("10/minute")
def refresh_cases(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RefreshOut:
    """Sync reviews with resolved coverage vs each product's Non-Evidence Limit.

    Recomputes the whole roster's coverage and writes/deletes rows, so it
    carries the repo's bulk-write rate limit (10/min) rather than the default.
    """
    py = assert_policy_year_for_user(policy_year_id, user, db)
    result = refresh_underwriting_cases(db, py)
    write_audit(
        db, user, action="refresh", entity_type="underwriting_case",
        entity_id=policy_year_id,
        after={
            "opened": result.opened,
            "updated": result.updated,
            "removed": result.removed,
        },
    )
    db.commit()
    return RefreshOut(
        opened=result.opened, updated=result.updated,
        removed=result.removed, open_cases=result.open_cases,
    )


@router.patch(
    "/underwriting/reviews/{review_id}", response_model=UnderwritingReviewOut
)
def update_review(
    review_id: str,
    body: UnderwritingReviewIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnderwritingReviewOut:
    """Case workflow status + requirements notes (the case-level header)."""
    review = db.get(UnderwritingReview, review_id)
    if review is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Underwriting case not found")
    if not user_owns(user, review.client_id):
        raise _deny_cross_tenant(user, "Underwriting review", review_id)
    if body.status is not None and body.status not in VALID_REVIEW_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"status must be one of {sorted(VALID_REVIEW_STATUSES)}",
        )
    before = {"status": review.status, "requirements": review.requirements}
    if body.status is not None:
        review.status = body.status
    if "requirements" in body.model_fields_set:
        review.requirements = (body.requirements or "").strip() or None
    review.modified_by = user.user_id
    db.flush()
    write_audit(
        db, user, action="update", entity_type="underwriting_review",
        entity_id=review.id, before=before,
        after={"status": review.status, "requirements": review.requirements},
    )
    db.commit()
    return _reload_review_out(db, review)


@router.patch(
    "/underwriting/cases/{case_id}", response_model=UnderwritingReviewOut
)
def decide_case(
    case_id: str,
    body: UnderwritingDecisionIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnderwritingReviewOut:
    """Record the insurer's per-product decision on one case line."""
    case = db.get(UnderwritingCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    if not user_owns(user, case.client_id):
        # Security-log the cross-tenant probe (like every loader in deps.py),
        # then return the same 404 so existence isn't leaked.
        raise _deny_cross_tenant(user, "Underwriting case", case_id)
    if body.status not in VALID_UW_STATUSES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"status must be one of {sorted(VALID_UW_STATUSES)}",
        )
    before = {
        "status": case.status,
        "accepted_si": case.accepted_si,
        "guaranteed_si": case.guaranteed_si,
        "decided_on": case.decided_on.isoformat() if case.decided_on else None,
    }
    if body.guaranteed_si is not None:
        case.guaranteed_si = min(body.guaranteed_si, case.eligible_si)
        case.guaranteed_overridden = True
    # Legacy lines (pre review model) carried the auto-covered FCL in
    # accepted_si — fall back to it so a decision can't zero the in-force SI.
    guaranteed = (
        case.guaranteed_si if case.guaranteed_si is not None else case.accepted_si
    )
    case.status = body.status
    if body.accepted_si is not None:
        case.accepted_si = min(body.accepted_si, case.eligible_si)
    elif body.status in _APPROVED_STATUSES:
        # An approval with no figure means the insurer took the request as
        # asked. Defaulting to the GUARANTEED amount instead would be the
        # dangerous read: the status is "decided", so the listing stops
        # reporting a pending excess, and the approved excess would silently
        # vanish from cover.
        case.accepted_si = case.eligible_si
    elif body.status in DECIDED_UW_STATUSES:
        # Excess refused / case closed — the guaranteed amount stays in force.
        case.accepted_si = min(guaranteed, case.eligible_si)
    else:
        # Undecided (pending / postponed) with no figure: guaranteed in force.
        case.accepted_si = min(guaranteed, case.eligible_si)
    case.decided_on = (
        (body.decided_on or date.today())
        if body.status in DECIDED_UW_STATUSES
        else None
    )
    if body.remarks is not None:
        case.remarks = body.remarks or None
    case.modified_by = user.user_id
    db.flush()
    write_audit(
        db, user, action="decide", entity_type="underwriting_case",
        entity_id=case.id, before=before,
        after={
            "status": case.status,
            "accepted_si": case.accepted_si,
            "guaranteed_si": case.guaranteed_si,
            "decided_on": case.decided_on.isoformat() if case.decided_on else None,
        },
    )
    db.commit()
    if case.review_id is None:
        # Pre-review-model row decided before anything adopted it — group it
        # now rather than refusing a decision the broker already recorded.
        adopt_orphan_cases(db, case.policy_year_id)
        db.commit()
    review = db.get(UnderwritingReview, case.review_id) if case.review_id else None
    if review is None:  # defensive: a subject-less row can't form a review
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Decision saved, but the case has no member to group it under.",
        )
    return _reload_review_out(db, review)


def _reload_review_out(db: Session, review: UnderwritingReview) -> UnderwritingReviewOut:
    lines = list(
        db.execute(
            select(UnderwritingCase).where(UnderwritingCase.review_id == review.id)
        ).scalars().all()
    )
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(Product.id.in_({c.product_id for c in lines}))
        ).scalars().all()
    } if lines else {}
    employees: dict[str, Employee] = {}
    dependants: dict[str, Dependant] = {}
    if review.employee_id:
        emp = db.get(Employee, review.employee_id)
        if emp:
            employees[emp.id] = emp
    elif review.dependant_id:
        dep = db.get(Dependant, review.dependant_id)
        if dep:
            dependants[dep.id] = dep
            if dep.employee_id:
                emp = db.get(Employee, dep.employee_id)
                if emp:
                    employees[emp.id] = emp
    return _review_out(review, lines, products, employees, dependants)
