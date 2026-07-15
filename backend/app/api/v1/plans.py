"""Plans CRUD — Schedule of Benefits per product plan."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    assert_policy_year_for_user,
    load_plan,
    require_broker_admin,
)
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.models import Plan, PolicyYear
from app.models.category import Category
from app.schemas.api import PlanFinancials
from app.services.plan_hydration import build_financials

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanOut(BaseModel):
    id: str
    product_id: str
    policy_year_id: str
    code: str
    display_name: str
    benefit_schedule: dict[str, Any] | None = None
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    # Premium / covered-amount figures sourced from the category that assigns
    # this plan code. None when no category assigns the plan or it has no
    # financial data. Used by the enrollment election UI.
    financials: PlanFinancials | None = None
    source: str
    confidence: float | None = None
    status: str
    human_modified: bool = False


class PlanList(BaseModel):
    total: int
    items: list[PlanOut]


class BenefitLimitIn(BaseModel):
    label: str
    value: str | None = None


class BenefitSubItemIn(BaseModel):
    key: str = ""
    name: str = ""
    value: str | None = None
    note: str | None = None
    limits: list[BenefitLimitIn] = []
    kind: str | None = None


class BenefitItemIn(BaseModel):
    number: str
    name: str
    value: str | None = None
    note: str | None = None
    limits: list[BenefitLimitIn] = []
    sub_items: list[BenefitSubItemIn] = []
    properties: dict[str, str] = {}


class BenefitScheduleIn(BaseModel):
    items: list[BenefitItemIn]


class PlanUpdate(BaseModel):
    display_name: str | None = None
    benefit_schedule: BenefitScheduleIn | None = None
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    status: str | None = None


def _plan_out(p: Plan, financials: PlanFinancials | None = None) -> PlanOut:
    return PlanOut(
        id=p.id,
        product_id=p.product_id,
        policy_year_id=p.policy_year_id,
        code=p.code,
        display_name=p.display_name,
        benefit_schedule=p.benefit_schedule,
        cover_description=p.cover_description,
        annual_policy_limit=p.annual_policy_limit,
        financials=financials,
        source=p.source,
        confidence=p.confidence,
        status=p.status,
        human_modified=p.human_modified,
    )


def _financials_by_plan(
    db: Session, policy_year_id: str
) -> dict[tuple[str, str], PlanFinancials]:
    """Map ``(product_id, plan_code) → PlanFinancials`` from category assignments.

    Categories own ``plan_assignments`` (sum insured, premium, rate); a plan
    inherits the figures of the category that assigns its code. When several
    cohorts assign the same plan, the first with financial data wins.
    """
    rows = db.execute(
        select(Category.product_id, Category.plan_assignments).where(
            Category.policy_year_id == policy_year_id
        )
    ).all()
    out: dict[tuple[str, str], PlanFinancials] = {}
    for product_id, pa in rows:
        if not product_id or not pa:
            continue
        code = pa.get("plan_code")
        if code is None:
            continue
        fin = build_financials(pa)
        if fin is None:
            continue
        key = (product_id, str(code))
        existing = out.get(key)
        # First with rate data wins — but a voluntary age-banded tier carries only
        # basis + voluntary_rates (no group SI/rate/premium), so it must not shadow
        # a compulsory sibling that assigns the same plan code with real figures.
        if existing is None or (not _has_rate_data(existing) and _has_rate_data(fin)):
            out[key] = fin
    return out


def _has_rate_data(fin: PlanFinancials) -> bool:
    """True when the plan carries displayable group rate figures (vs a voluntary
    age-banded tier that only knows a per-member basis + age-band table)."""
    return any(
        v is not None
        for v in (fin.sum_insured, fin.premium_rate, fin.annual_premium, fin.rate_tiers)
    )


@router.get("", response_model=PlanList)
def list_plans(
    policy_year_id: str,
    product_id: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PlanList:
    assert_policy_year_for_user(policy_year_id, user, db)
    filters = [Plan.policy_year_id == policy_year_id]
    if product_id:
        filters.append(Plan.product_id == product_id)
    total = db.execute(
        select(func.count(Plan.id)).where(*filters)
    ).scalar_one()
    plans = list(
        db.execute(
            select(Plan)
            .where(*filters)
            .order_by(Plan.product_id, Plan.code)
            .offset(offset)
            .limit(limit)
        ).scalars().all()
    )
    fin = _financials_by_plan(db, policy_year_id)
    return PlanList(
        total=total,
        items=[_plan_out(p, fin.get((p.product_id, p.code))) for p in plans],
    )


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan: Plan = Depends(load_plan)) -> PlanOut:
    return _plan_out(plan)


@router.patch("/{plan_id}", response_model=PlanOut)
def update_plan(
    body: PlanUpdate,
    plan: Plan = Depends(load_plan),
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> PlanOut:
    py = db.get(PolicyYear, plan.policy_year_id)
    if py is not None:
        assert_policy_year_editable(py)
    before = {"benefit_schedule": plan.benefit_schedule, "status": plan.status}
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(plan, field, value)
    # benefit_schedule is validated as BenefitScheduleIn then stored as raw dict
    if "benefit_schedule" in updates and updates["benefit_schedule"] is not None:
        plan.benefit_schedule = body.benefit_schedule.model_dump()  # type: ignore[union-attr]
    if updates:
        plan.human_modified = True
        plan.modified_by = user.user_id

    write_audit(
        db, user,
        action="update",
        entity_type="plan",
        entity_id=plan.id,
        before=before,
        after=updates,
    )
    db.commit()
    db.refresh(plan)
    return _plan_out(plan)
