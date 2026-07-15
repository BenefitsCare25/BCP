"""Lightweight per-employee product-coverage summary (no SOB hydration).

The Benefit Statement filter/picker needs the *whole* roster's match counts and
matched products, but not the benefit schedules. This resolves
``matched_categories`` → product code/name with two bulk queries, skipping the
per-plan schedule loads that ``plan_hydration`` does — so the full roster stays
cheap to compute.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.models.category import Category
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.models.product import Product
from app.schemas.api import CoverageProduct, CoverageSummaryItem


def build_coverage_items(db: Session, policy_year_id: str) -> list[CoverageSummaryItem]:
    """Return one summary row per active employee in the policy year, ordered by staff ID."""
    rows = db.execute(
        select(
            Employee.id,
            Employee.staff_id,
            Employee.employee_name,
            Employee.matched_categories,
        )
        .where(
            Employee.policy_year_id == policy_year_id,
            Employee.status == EMPLOYEE_STATUS_ACTIVE,
        )
        .order_by(Employee.staff_id)
    ).all()

    cat_ids: set[str] = set()
    for _id, _sid, _name, matched in rows:
        for m in matched or []:
            if m.get("category_id"):
                cat_ids.add(m["category_id"])

    prod_by_cat: dict[str, tuple[str | None, str | None]] = {}
    if cat_ids:
        for cid, code, name in db.execute(
            select(Category.id, Product.code, Product.display_name)
            .outerjoin(Product, Category.product_id == Product.id)
            .where(Category.id.in_(cat_ids))
        ).all():
            prod_by_cat[cid] = (code, name)

    items: list[CoverageSummaryItem] = []
    for emp_id, staff_id, employee_name, matched in rows:
        seen: dict[str, str | None] = {}
        for m in matched or []:
            cid = m.get("category_id")
            code = prod_name = None
            if cid and cid in prod_by_cat:
                code, prod_name = prod_by_cat[cid]
            code = code or m.get("product_code")
            if not code:
                continue
            seen.setdefault(code, prod_name)
        products = [
            CoverageProduct(product_code=c, product_name=n) for c, n in seen.items()
        ]
        items.append(
            CoverageSummaryItem(
                id=emp_id,
                staff_id=staff_id,
                employee_name=employee_name,
                product_count=len(products),
                products=products,
            )
        )
    return items
