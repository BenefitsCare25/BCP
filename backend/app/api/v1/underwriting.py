"""Underwriting queue — free-cover-limit cases + broker decisions.

OPERATIONAL surface (not activation-locked): cases exist precisely because a
live year's members exceed a product's free cover limit. Decisions feed the
insurer listings' "Sum Insured Pending U/W" / "Last Accepted Sum Insured".
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
from app.models import Dependant, Employee, Product, UnderwritingCase
from app.models.underwriting_case import VALID_UW_STATUSES, UnderwritingStatus
from app.services.roster_attributes import NAME_KEYS, first_value
from app.services.underwriting import free_cover_limits, refresh_underwriting_cases

router = APIRouter(tags=["underwriting"])


class UnderwritingCaseOut(BaseModel):
    id: str
    product_id: str
    product_code: str
    subject_type: str  # "employee" | "dependant"
    subject_name: str | None
    staff_id: str | None
    eligible_si: float
    accepted_si: float
    pending_si: float
    free_cover_limit: float | None
    status: str
    decided_on: date | None
    remarks: str | None


class UnderwritingQueueOut(BaseModel):
    total: int
    pending: int
    items: list[UnderwritingCaseOut]


class UnderwritingDecisionIn(BaseModel):
    status: str
    accepted_si: float | None = Field(default=None, ge=0)
    decided_on: date | None = None
    remarks: str | None = Field(default=None, max_length=1024)


class RefreshOut(BaseModel):
    opened: int
    updated: int
    removed: int
    open_cases: int


def _case_out(
    case: UnderwritingCase,
    products: dict[str, Product],
    employees: dict[str, Employee],
    dependants: dict[str, Dependant],
    fcl: dict[str, float],
) -> UnderwritingCaseOut:
    if case.employee_id:
        emp = employees.get(case.employee_id)
        subject_type = "employee"
        subject_name = emp.employee_name if emp else None
        staff_id = emp.staff_id if emp else None
    else:
        dep = dependants.get(case.dependant_id or "")
        emp = employees.get(dep.employee_id) if dep and dep.employee_id else None
        subject_type = "dependant"
        subject_name = (
            first_value(dep.attribute_values or {}, NAME_KEYS) if dep else None
        )
        staff_id = emp.staff_id if emp else None
    pending = (
        max(case.eligible_si - min(case.accepted_si, case.eligible_si), 0.0)
        if case.status == UnderwritingStatus.pending
        else 0.0
    )
    product = products.get(case.product_id)
    return UnderwritingCaseOut(
        id=case.id,
        product_id=case.product_id,
        product_code=product.code if product else "?",
        subject_type=subject_type,
        subject_name=subject_name,
        staff_id=staff_id,
        eligible_si=case.eligible_si,
        accepted_si=case.accepted_si,
        pending_si=pending,
        free_cover_limit=fcl.get(case.product_id),
        status=case.status,
        decided_on=case.decided_on,
        remarks=case.remarks,
    )


def _queue(db: Session, policy_year_id: str) -> UnderwritingQueueOut:
    cases = list(
        db.execute(
            select(UnderwritingCase)
            .where(UnderwritingCase.policy_year_id == policy_year_id)
            .order_by(UnderwritingCase.status.desc(), UnderwritingCase.created_at)
        ).scalars().all()
    )
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in cases})
            )
        ).scalars().all()
    } if cases else {}
    emp_ids = {c.employee_id for c in cases if c.employee_id}
    dep_ids = {c.dependant_id for c in cases if c.dependant_id}
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
    fcl = free_cover_limits(db, policy_year_id)
    items = [_case_out(c, products, employees, dependants, fcl) for c in cases]
    return UnderwritingQueueOut(
        total=len(items),
        pending=sum(1 for i in items if i.status == UnderwritingStatus.pending),
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
    """Sync cases with resolved coverage vs each product's free cover limit.

    Recomputes the whole roster's coverage and writes/deletes case rows, so it
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
    "/underwriting/cases/{case_id}", response_model=UnderwritingCaseOut
)
def decide_case(
    case_id: str,
    body: UnderwritingDecisionIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UnderwritingCaseOut:
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
        "decided_on": case.decided_on.isoformat() if case.decided_on else None,
    }
    case.status = body.status
    if body.accepted_si is not None:
        case.accepted_si = min(body.accepted_si, case.eligible_si)
    if body.status != UnderwritingStatus.pending:
        case.decided_on = body.decided_on or date.today()
    else:
        case.decided_on = None
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
            "decided_on": case.decided_on.isoformat() if case.decided_on else None,
        },
    )
    db.commit()
    queue = _queue(db, case.policy_year_id)
    out = next((i for i in queue.items if i.id == case.id), None)
    if out is None:  # defensive: a decided case is never dropped from the queue
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Decision saved but the case could not be re-read.",
        )
    return out
