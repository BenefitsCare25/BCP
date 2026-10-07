"""Read-only mapping summaries and plans missing an employee category."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Category, Employee, Plan, PolicyYear, Product
from app.services.eligibility_mapping.base import (
    MappingItem,
    MappingSummary,
    MissingCategoryPlan,
)
from app.services.eligibility_mapping.predicates import (
    is_bulk_confirmable,
    is_employee_mapping_category,
)


def missing_category_plans(db: Session, *, policy_year_id: str) -> list[MissingCategoryPlan]:
    """Return materialized plans that no employee category assigns."""

    categories = list(
        db.execute(select(Category).where(Category.policy_year_id == policy_year_id)).scalars()
    )
    assigned = {
        (
            category.product_id,
            str((category.plan_assignments or {}).get("plan_code") or "").strip().casefold(),
        )
        for category in categories
        if category.product_id and isinstance(category.plan_assignments, dict)
    }
    plans = list(
        db.execute(
            select(Plan)
            .where(Plan.policy_year_id == policy_year_id)
            .order_by(Plan.product_id, Plan.code)
        ).scalars()
    )
    client_id = db.scalar(select(PolicyYear.client_id).where(PolicyYear.id == policy_year_id))
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({plan.product_id for plan in plans}),
                tenant_or_global(Product.client_id, client_id),
            )
        ).scalars()
    }
    missing: list[MissingCategoryPlan] = []
    for plan in plans:
        if (plan.product_id, plan.code.strip().casefold()) in assigned:
            continue
        product = products.get(plan.product_id)
        if product is None:
            continue
        missing.append(
            MissingCategoryPlan(
                plan_id=plan.id,
                product_id=plan.product_id,
                product_code=product.code,
                product_display_name=product.display_name,
                plan_code=plan.code,
                plan_display_name=plan.display_name,
                source_hint=plan.cover_description,
            )
        )
    return missing


def stored_mapping_summary(db: Session, *, policy_year_id: str) -> MappingSummary:
    """Read the last persisted mapping/validation state without side effects."""

    all_categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    categories = [
        category for category in all_categories if is_employee_mapping_category(category)
    ]
    not_applicable = len(all_categories) - len(categories)
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    items: list[MappingItem] = []
    for category in categories:
        validation = category.rule_validation if isinstance(category.rule_validation, dict) else {}
        pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
        expected_raw = validation.get("expected_count", pa.get("num_employees"))
        matched_raw = validation.get("matched_count")
        expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
        matched = int(matched_raw) if isinstance(matched_raw, (int, float)) else None
        rule_status = str(
            category.rule_status or ("unmapped" if category.matching_rule is None else "proposed")
        )
        product = products.get(category.product_id) if category.product_id else None
        items.append(
            MappingItem(
                category_id=category.id,
                product_code=product.code if product else None,
                display_name=category.display_name,
                plan_code=str(pa.get("plan_code") or "") or None,
                category_status=category.status,
                rule_status=rule_status,
                source=str(validation.get("source") or category.source),
                matching_rule=category.matching_rule,
                rule_human_readable=category.rule_human_readable,
                confidence=category.confidence,
                matched_count=matched,
                expected_count=expected,
                unresolved_clauses=[
                    str(value) for value in validation.get("unresolved_clauses", [])
                ],
                errors=[str(value) for value in validation.get("errors", [])],
                warnings=[str(value) for value in validation.get("warnings", [])],
                reused=bool(validation.get("reused")),
                bulk_confirmable=is_bulk_confirmable(category),
            )
        )
    employee_count = db.execute(
        select(Employee.id).where(Employee.policy_year_id == policy_year_id)
    ).all()
    missing = missing_category_plans(db, policy_year_id=policy_year_id)
    return MappingSummary(
        policy_year_id=policy_year_id,
        employee_count=len(employee_count),
        total=len(items),
        validated=sum(item.rule_status == "validated" for item in items),
        proposed=sum(item.rule_status == "proposed" for item in items),
        needs_review=sum(item.rule_status == "needs_review" for item in items),
        unmapped=sum(item.rule_status == "unmapped" for item in items),
        not_applicable=not_applicable,
        reused=sum(item.reused for item in items),
        categories=items,
        missing_categories=len(missing),
        missing_category_plans=missing,
    )
