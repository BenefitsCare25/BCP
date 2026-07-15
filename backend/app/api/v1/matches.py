"""Match results — Phase 5 endpoints.

GET  /api/v1/match-results  — counts + paginated per-employee items.
POST /api/v1/match-results/run — re-derive + re-match every employee in a policy year.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user, load_employee, require_client_id
from app.core.pagination import MAX_LIMIT
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import AuditLog, Category, Employee
from app.models.product import Product
from app.schemas.api import (
    MatchOverridePayload,
    MatchResultItem,
    MatchResultsOut,
    MatchRunResult,
)
from app.services.matching_engine import match_policy_year

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/match-results", tags=["match-results"])


@router.get("", response_model=MatchResultsOut)
def get_match_results(
    policy_year_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchResultsOut:
    assert_policy_year_for_user(policy_year_id, user, db)
    counts = db.execute(
        select(
            func.count(Employee.id),
            func.count(Employee.matched_category_id),
        ).where(Employee.policy_year_id == policy_year_id)
    ).one()
    total, matched = counts[0] or 0, counts[1] or 0

    last_run_row = db.execute(
        select(AuditLog)
        .where(AuditLog.action == "run_matching", AuditLog.entity_type == "policy_year")
        .where(AuditLog.entity_id == policy_year_id)
        .order_by(desc(AuditLog.created_at))
        .limit(1)
    ).scalar_one_or_none()
    last_run_at = last_run_row.created_at if last_run_row else None

    never_run = last_run_row is None
    # Matches are STALE (not just "never run") when any category changed after
    # the last run — re-parses, rule edits and tier changes all bump
    # Category.updated_at, and matched_categories snapshots don't self-heal.
    stale = False
    if not never_run:
        latest_category_change = db.scalar(
            select(func.max(Category.updated_at)).where(
                Category.policy_year_id == policy_year_id
            )
        )
        stale = (
            latest_category_change is not None
            and last_run_at is not None
            and latest_category_change > last_run_at
        )
    pending = never_run or stale

    item_total = total
    if never_run:
        items: list[MatchResultItem] = []
    else:
        # Join employees + their matched category (LEFT OUTER) so we can show
        # the display name in one round trip.
        rows = list(
            db.execute(
                select(Employee, Category)
                .join(Category, Employee.matched_category_id == Category.id, isouter=True)
                .where(Employee.policy_year_id == policy_year_id)
                .order_by(Employee.matched_category_id.is_(None).desc(), Employee.staff_id)
                .offset(offset)
                .limit(limit)
            ).all()
        )
        items = [
            MatchResultItem(
                employee_id=emp.id,
                employee_name=emp.employee_name,
                staff_id=emp.staff_id,
                raw_category=(emp.attribute_values or {}).get("category"),
                matched_category_id=emp.matched_category_id,
                matched_category_display=cat.display_name if cat else None,
                match_method=emp.match_method,
                match_confidence=emp.match_confidence,
            )
            for emp, cat in rows
        ]

    reason = None
    if never_run:
        reason = "Matching has not been run for this policy year yet."
    elif stale:
        reason = (
            "Categories changed after the last matching run — results may be "
            "stale. Re-run matching."
        )
    return MatchResultsOut(
        pending=pending,
        reason=reason,
        employees_total=total,
        employees_matched=matched,
        employees_unmatched=total - matched,
        last_run_at=last_run_at,
        items=items,
        items_total=item_total,
        offset=offset,
        limit=limit,
    )


@router.post("/run", response_model=MatchRunResult)
@limiter.limit("10/minute")
def run_matching(
    request: Request,
    policy_year_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchRunResult:
    require_client_id(user)
    assert_policy_year_for_user(policy_year_id, user, db)

    summary = match_policy_year(db, policy_year_id, user)
    write_audit(
        db,
        user,
        action="run_matching",
        entity_type="policy_year",
        entity_id=policy_year_id,
        after={
            "employees_total": summary.employees_total,
            "employees_matched": summary.employees_matched,
            "by_method": summary.by_method,
            "duration_ms": summary.duration_ms,
            "errors": summary.errors,
        },
    )
    db.commit()
    if summary.errors:
        logger.error(
            "Matching run for policy year %s hit %d per-employee errors "
            "(reported to the caller — these are match FAILURES, not unmatched).",
            policy_year_id, summary.errors,
        )
    return MatchRunResult(
        employees_total=summary.employees_total,
        employees_matched=summary.employees_matched,
        employees_unmatched=summary.employees_total - summary.employees_matched,
        by_method=summary.by_method,
        duration_ms=summary.duration_ms,
        errors=summary.errors,
    )


def _bulk_override(
    category_ids: list[str],
    employee: Employee,
    user: CurrentUser,
    db: Session,
) -> MatchResultItem:
    """Replace an employee's entire manual match set with the given categories.

    One category per product (duplicates → 422). Each must belong to the
    employee's policy year. An empty list clears all matches.
    """
    categories: list[Category] = []
    seen_products: set[str] = set()
    for cid in category_ids:
        cat = db.get(Category, cid)
        if cat is None or cat.policy_year_id != employee.policy_year_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Category {cid!r} not found for this policy year",
            )
        product_key = cat.product_id or "__no_product__"
        if product_key in seen_products:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Cannot assign two categories of the same product to one employee.",
            )
        seen_products.add(product_key)
        categories.append(cat)

    entries: list[dict] = []
    for cat in categories:
        prod = db.get(Product, cat.product_id) if cat.product_id else None
        entries.append({
            "category_id": cat.id,
            "product_code": prod.code if prod else "?",
            "method": "manual_override",
            "confidence": 1.0,
        })

    before = {
        "matched_category_id": employee.matched_category_id,
        "match_method": employee.match_method,
    }
    employee.matched_categories = entries or None
    employee.matched_category_id = entries[0]["category_id"] if entries else None
    employee.match_method = "manual_override" if entries else None
    employee.match_confidence = 1.0 if entries else None
    db.flush()
    write_audit(
        db, user,
        action="override_match" if entries else "clear_match",
        entity_type="employee", entity_id=employee.id, before=before,
        after={"matched_category_ids": [c.id for c in categories],
               "match_method": employee.match_method},
    )
    db.commit()
    db.refresh(employee)
    return MatchResultItem(
        employee_id=employee.id,
        employee_name=employee.employee_name,
        staff_id=employee.staff_id,
        raw_category=(employee.attribute_values or {}).get("category"),
        matched_category_id=employee.matched_category_id,
        matched_category_display=categories[0].display_name if categories else None,
        match_method=employee.match_method,
        match_confidence=employee.match_confidence,
    )


@router.post("/employees/{employee_id}/override", response_model=MatchResultItem)
def override_match(
    payload: MatchOverridePayload,
    employee: Employee = Depends(load_employee),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchResultItem:
    """Pin an employee to a specific category, or clear the match.

    `category_id=null` clears the match. Otherwise the chosen category must
    belong to the same policy year as the employee. Method is recorded as
    `manual_override` and confidence is set to 1.0 so the UI badges this as
    operator-verified.

    When `category_ids` is supplied it takes precedence and REPLACES the whole
    manual match set (one category per product). An empty list clears all.
    """
    if payload.category_ids is not None:
        return _bulk_override(payload.category_ids, employee, user, db)

    # `load_employee` already proved tenant ownership; matching policy_year_id
    # therefore proves tenant ownership of the category too.
    category: Category | None = None
    if payload.category_id is not None:
        category = db.get(Category, payload.category_id)
        if category is None or category.policy_year_id != employee.policy_year_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Category not found for this policy year"
            )

    before = {
        "matched_category_id": employee.matched_category_id,
        "match_method": employee.match_method,
        "match_confidence": employee.match_confidence,
    }
    employee.matched_category_id = category.id if category else None
    employee.match_method = "manual_override" if category else None
    employee.match_confidence = 1.0 if category else None
    if category:
        prod = db.get(Product, category.product_id) if category.product_id else None
        override_entry = {
            "category_id": category.id,
            "product_code": prod.code if prod else "?",
            "method": "manual_override",
            "confidence": 1.0,
        }
        existing = employee.matched_categories or []
        updated = [m for m in existing if m.get("product_code") != (prod.code if prod else None)]
        updated.append(override_entry)
        employee.matched_categories = updated
    else:
        employee.matched_categories = None
    db.flush()
    write_audit(
        db,
        user,
        action="override_match" if category else "clear_match",
        entity_type="employee",
        entity_id=employee.id,
        before=before,
        after={
            "matched_category_id": employee.matched_category_id,
            "match_method": employee.match_method,
            "match_confidence": employee.match_confidence,
        },
    )
    db.commit()
    db.refresh(employee)
    return MatchResultItem(
        employee_id=employee.id,
        employee_name=employee.employee_name,
        staff_id=employee.staff_id,
        raw_category=(employee.attribute_values or {}).get("category"),
        matched_category_id=employee.matched_category_id,
        matched_category_display=category.display_name if category else None,
        match_method=employee.match_method,
        match_confidence=employee.match_confidence,
    )
