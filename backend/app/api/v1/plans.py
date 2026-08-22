"""Plans CRUD — Schedule of Benefits per product plan."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import (
    assert_policy_year_editable,
    assert_policy_year_for_user,
    load_plan,
    require_broker_admin,
    require_client_id,
    tenant_or_global,
)
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.models import Plan, PolicyYear, ProductSetup
from app.models.category import Category
from app.models.product import Product
from app.schemas.api import PlanFinancials
from app.services.benefit_key_guard import (
    orphan_conflict_detail,
    orphaned_benefit_keys,
)
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
    report_label: str | None = None
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


class PlanCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=36)
    policy_year_id: str = Field(min_length=1, max_length=36)
    display_name: str = Field(min_length=1, max_length=255)
    report_label: str | None = Field(default=None, max_length=255)


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
    # The value's type (currency/percent/days/list/…). Persisted because it is
    # the only type signal the read-only renderers have; without it they must
    # guess from the string and render a visit count as a dollar amount.
    kind: str | None = None


class BenefitScheduleIn(BaseModel):
    items: list[BenefitItemIn]


class PlanUpdate(BaseModel):
    display_name: str | None = None
    benefit_schedule: BenefitScheduleIn | None = None
    cover_description: str | None = None
    annual_policy_limit: str | None = None
    report_label: str | None = None
    status: str | None = None
    # Proceed even though the new schedule drops a benefit name that existing
    # claims reference (409 `orphaned_benefit_keys` otherwise).
    acknowledge: bool = False


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
        report_label=p.report_label,
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


def _next_plan_code(plans: list[Plan]) -> str:
    used = {plan.code.strip().casefold() for plan in plans}
    numeric = [int(code) for code in used if code.isdigit()]
    candidate = max(numeric, default=0) + 1
    while str(candidate).casefold() in used:
        candidate += 1
    return str(candidate)


def _append_plan_to_setup(setup: ProductSetup, plan: Plan) -> None:
    answers = deepcopy(setup.answers or {})
    plans = answers.get("plans")
    if not isinstance(plans, list):
        plans = []
        answers["plans"] = plans
    if not any(
        isinstance(item, dict) and str(item.get("code") or "") == plan.code
        for item in plans
    ):
        plans.append(
            {
                "code": plan.code,
                "label": plan.display_name,
                "selected": True,
            }
        )

    sob = answers.get("sob")
    if isinstance(sob, dict):
        columns = sob.get("columns")
        if not isinstance(columns, list):
            columns = []
            sob["columns"] = columns
        if len(columns) == 1 and isinstance(columns[0], dict):
            plan_codes = columns[0].get("plan_codes")
            if not isinstance(plan_codes, list):
                plan_codes = []
                columns[0]["plan_codes"] = plan_codes
            if plan.code not in {str(value) for value in plan_codes}:
                plan_codes.append(plan.code)
        elif not any(
            isinstance(column, dict)
            and plan.code in {str(value) for value in column.get("plan_codes", [])}
            for column in columns
        ):
            columns.append(
                {
                    "id": f"plan-{plan.id}",
                    "label": plan.display_name,
                    "plan_codes": [plan.code],
                }
            )
    setup.answers = answers


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


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(
    body: PlanCreate,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> PlanOut:
    py = assert_policy_year_for_user(body.policy_year_id, user, db)
    assert_policy_year_editable(py)
    client_id = require_client_id(user)
    product = db.execute(
        select(Product).where(
            Product.id == body.product_id,
            tenant_or_global(Product.client_id, client_id),
        )
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    setup = db.execute(
        select(ProductSetup)
        .where(
            ProductSetup.policy_year_id == py.id,
            ProductSetup.product_code == product.code.upper(),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if setup is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Save the product setup before adding a plan type",
        )

    display_name = body.display_name.strip()
    if not display_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Plan name is required")
    report_label = (body.report_label or "").strip() or None
    existing = list(
        db.execute(
            select(Plan)
            .where(
                Plan.policy_year_id == py.id,
                Plan.product_id == product.id,
            )
            .with_for_update()
        ).scalars()
    )
    plan = Plan(
        product_id=product.id,
        policy_year_id=py.id,
        code=_next_plan_code(existing),
        display_name=display_name,
        report_label=report_label,
        source="manual",
        status="needs_review",
        human_modified=True,
        modified_by=user.user_id,
    )
    try:
        db.add(plan)
        db.flush()
        _append_plan_to_setup(setup, plan)
        write_audit(
            db,
            user,
            action="create",
            entity_type="plan",
            entity_id=plan.id,
            after={
                "policy_year_id": py.id,
                "product_id": product.id,
                "code": plan.code,
                "display_name": plan.display_name,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Another plan was added concurrently; please retry",
        ) from None
    db.refresh(plan)
    return _plan_out(plan)


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
    updates = body.model_dump(exclude_unset=True)
    acknowledge = bool(updates.pop("acknowledge", False))
    py = db.get(PolicyYear, plan.policy_year_id)
    # report_label is OPERATIONAL metadata (insurer report display text) — it
    # doesn't alter coverage, so a label-only patch stays editable after
    # activation. Anything touching real plan config keeps the lock.
    if py is not None and not set(updates) <= {"report_label"}:
        assert_policy_year_editable(py)
    # Renaming/removing a benefit line strands claims that reference it by name
    # (see services/benefit_key_guard). Confirmable, not a hard block.
    if body.benefit_schedule is not None and not acknowledge:
        product = db.get(Product, plan.product_id)
        orphaned = orphaned_benefit_keys(
            db,
            policy_year_id=plan.policy_year_id,
            product_code=product.code if product else None,
            new_items=[i.model_dump() for i in body.benefit_schedule.items],
        )
        if orphaned:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=orphan_conflict_detail(
                    orphaned, product.code if product else ""
                ),
            )

    before = {"benefit_schedule": plan.benefit_schedule, "status": plan.status}
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
