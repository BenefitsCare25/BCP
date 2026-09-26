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
from app.services.voluntary_enrolment import employee_participation, enrolled_products


def build_coverage_items(
    db: Session, policy_year_id: str, *, include_left: bool = False
) -> list[CoverageSummaryItem]:
    """One summary row per employee in the policy year, ordered by staff ID.

    Active only by default — a roster page is about the people currently
    covered. ``include_left`` adds the terminated ones, which is not a
    convenience: this picker is the ONLY surface that mounts
    ``MemberAccountActions``, and every leaver phase that sheet renders
    (``leaving``/``left``/``settling``/``ended``, the wind-down dates, the
    derived ``access_state``) is about someone this filter had already removed
    from the page — so the whole leaver half of it could never be reached. A
    broker settling a leaver's last claim needs exactly that person.
    """
    conditions = [Employee.policy_year_id == policy_year_id]
    if not include_left:
        conditions.append(Employee.status == EMPLOYEE_STATUS_ACTIVE)
    rows = db.execute(
        select(
            Employee.id,
            Employee.staff_id,
            Employee.employee_name,
            Employee.matched_categories,
            Employee.status,
        )
        .where(*conditions)
        .order_by(Employee.staff_id)
    ).all()

    cat_ids: set[str] = set()
    for _id, _sid, _name, matched, _status in rows:
        for m in matched or []:
            if m.get("category_id"):
                cat_ids.add(m["category_id"])

    prod_by_cat: dict[str, tuple[str | None, str | None, str | None, bool]] = {}
    if cat_ids:
        for cat, code, name in db.execute(
            select(Category, Product.code, Product.display_name)
            .outerjoin(Product, Category.product_id == Product.id)
            .where(Category.id.in_(cat_ids))
        ).all():
            prod_by_cat[cat.id] = (
                code, name, cat.product_id, employee_participation(cat) == "voluntary"
            )
    enrolled = enrolled_products(db, policy_year_id, [row[0] for row in rows])

    items: list[CoverageSummaryItem] = []
    for emp_id, staff_id, employee_name, matched, status in rows:
        seen: dict[str, str | None] = {}
        eligible: set[str] = set()
        needs_check = False
        for m in matched or []:
            cid = m.get("category_id")
            code = prod_name = product_id = None
            voluntary = False
            if cid and cid in prod_by_cat:
                code, prod_name, product_id, voluntary = prod_by_cat[cid]
            code = code or m.get("product_code")
            if not code:
                continue
            if voluntary and (emp_id, product_id) not in enrolled:
                eligible.add(code)
                continue
            seen.setdefault(code, prod_name)
            confidence = m.get("confidence")
            if m.get("method") == "fuzzy_name" and not (
                isinstance(confidence, (int, float)) and confidence >= 1
            ):
                needs_check = True
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
                eligible_count=len(eligible - seen.keys()),
                needs_check=needs_check,
                # Served ALWAYS, not only when leavers were asked for: the
                # picker marks the row, and a row that looks identical to an
                # active colleague's is how a broker reads a leaver's coverage
                # as current.
                left=status != EMPLOYEE_STATUS_ACTIVE,
            )
        )
    return items
