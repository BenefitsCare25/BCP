"""Fail-closed checks for the draft -> open enrollment transition.

The member experience combines coverage matching, product identity, portal
access, and (optionally) Flex funding. Opening is the irreversible boundary at
which those independent drafts become one employee-facing promise, so this
module reports aggregate, non-PII blockers before any enrollment rows are made.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, FlexScheme, MemberAccount, Product
from app.models.category import CategoryStatus
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.models.enrollment_window import EnrollmentWindow, FlexDrawdownRule
from app.models.flex_scheme import FlexSchemeStatus
from app.services.cohort_tiers import list_product_tiers
from app.services.flex_pricing_resolver import (
    employee_age,
    get_pricing,
    maybe_slip_index,
    member_price_tag,
    reference_date,
    window_flex_config,
)


def _issue(
    code: str,
    message: str,
    *,
    count: int | None = None,
    products: set[str] | list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "message": message}
    if count is not None:
        out["count"] = count
    if products:
        out["products"] = sorted(set(products))
    return out


def enrollment_readiness_issues(
    db: Session, window: EnrollmentWindow
) -> list[dict[str, Any]]:
    """Return aggregate blockers for opening ``window`` (empty means ready).

    Nothing in the result identifies an employee. This object is safe for the
    broker UI and operational logs while still making each remediation concrete.
    """
    issues: list[dict[str, Any]] = []
    all_categories = list(
        db.scalars(
            select(Category).where(
                Category.policy_year_id == window.policy_year_id,
                Category.product_id.is_not(None),
            )
        ).all()
    )
    requested_scope = (
        {str(value) for value in window.product_scope if value}
        if isinstance(window.product_scope, list)
        else None
    )
    categories = [
        category
        for category in all_categories
        if requested_scope is None or category.product_id in requested_scope
    ]
    product_ids = {
        category.product_id for category in categories if category.product_id is not None
    }
    if not product_ids:
        if bool(window.uses_flex):
            issues.append(
                _issue(
                    "no_products_in_scope",
                    "No configured benefit products are in scope for this period.",
                )
            )
        return issues

    products = list(db.scalars(select(Product).where(Product.id.in_(product_ids))).all())
    code_by_product = {product.id: product.code for product in products}
    product_ids_by_code: dict[str, set[str]] = {}
    display_code: dict[str, str] = {}
    for product in products:
        key = product.code.strip().casefold()
        product_ids_by_code.setdefault(key, set()).add(product.id)
        display_code.setdefault(key, product.code.strip().upper())
    duplicate_codes = {
        display_code[key]
        for key, ids in product_ids_by_code.items()
        if len(ids) > 1
    }
    if duplicate_codes:
        issues.append(
            _issue(
                "duplicate_product_codes",
                "One benefit code resolves to multiple product records. Reconcile the "
                "product setup before opening.",
                count=len(duplicate_codes),
                products=duplicate_codes,
            )
        )

    employees = list(
        db.scalars(
            select(Employee).where(
                Employee.policy_year_id == window.policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        ).all()
    )
    if not employees:
        if bool(window.uses_flex):
            issues.append(
                _issue(
                    "no_active_employees",
                    "No active employees are available for this enrollment period.",
                )
            )
        return issues

    category_product = {category.id: category.product_id for category in categories}
    matched_products_by_employee: dict[str, set[str]] = {}
    matched_category_ids: set[str] = set()
    unmatched = 0
    for employee in employees:
        matched_products = {
            category_product.get(str(match.get("category_id") or ""))
            for match in (employee.matched_categories or [])
            if isinstance(match, dict)
        }
        matched = {pid for pid in matched_products if pid in product_ids}
        matched_category_ids.update(
            str(match.get("category_id"))
            for match in (employee.matched_categories or [])
            if isinstance(match, dict)
            and str(match.get("category_id") or "") in category_product
        )
        matched_products_by_employee[employee.id] = matched
        if not matched:
            unmatched += 1
    if unmatched:
        issues.append(
            _issue(
                "employees_without_coverage",
                "Active employees remain unmatched to every product in scope.",
                count=unmatched,
            )
        )

    unconfirmed = [
        category
        for category in categories
        if category.id in matched_category_ids
        and category.status != CategoryStatus.confirmed.value
    ]
    if unconfirmed:
        issues.append(
            _issue(
                "unconfirmed_categories",
                "Eligibility mappings currently assigned to employees still require "
                "broker review and confirmation.",
                count=len(unconfirmed),
                products={
                    code_by_product.get(category.product_id or "", "Unknown")
                    for category in unconfirmed
                },
            )
        )

    if not bool(window.uses_flex):
        return issues

    if window.member_self_service:
        accounts = list(
            db.scalars(
                select(MemberAccount).where(MemberAccount.client_id == window.client_id)
            ).all()
        )
        by_id = {account.id: account for account in accounts}
        by_staff: dict[str, MemberAccount] = {}
        for account in sorted(accounts, key=lambda row: (row.staff_id or "", row.id)):
            by_staff.setdefault(account.staff_id, account)
        inaccessible = 0
        for employee in employees:
            member_account = by_id.get(employee.member_account_id or "") or by_staff.get(
                employee.staff_id
            )
            usable = member_account is not None and (
                member_account.status == "active"
                or (
                    member_account.status == "invited"
                    and member_account.invite_sent_at is not None
                )
            )
            if not usable:
                inaccessible += 1
        if inaccessible:
            issues.append(
                _issue(
                    "portal_access_incomplete",
                    "Some active employees have no usable portal account or delivered invite.",
                    count=inaccessible,
                )
            )

    scheme = db.scalar(
        select(FlexScheme).where(FlexScheme.policy_year_id == window.policy_year_id)
    )
    if scheme is None or scheme.status != FlexSchemeStatus.confirmed:
        issues.append(
            _issue(
                "flex_scheme_not_confirmed",
                "Confirm the Flex scheme before opening a Flex-funded period.",
            )
        )

    wallets_missing = sum(
        1
        for employee in employees
        if employee.flex_wallet_amount is None or not employee.flex_currency
    )
    if wallets_missing:
        issues.append(
            _issue(
                "flex_wallets_incomplete",
                "Assign a wallet amount and currency to every active employee.",
                count=wallets_missing,
            )
        )

    # A duplicate code makes ``list_product_tiers`` ambiguous by definition.
    # Report that primary integrity error without producing a misleading price
    # count from a dictionary where one duplicate would overwrite the other.
    if duplicate_codes:
        return issues

    pricing = get_pricing(db, window.policy_year_id)
    source_map, _configured_rule = window_flex_config(window)
    slip_index = maybe_slip_index(db, window.policy_year_id, source_map)
    ref = reference_date(db, window.policy_year_id)
    ages_by_product: dict[str, set[int | None]] = {pid: set() for pid in product_ids}
    for employee in employees:
        age = employee_age(employee, ref)
        for product_id in matched_products_by_employee[employee.id]:
            ages_by_product[product_id].add(age)

    missing_tiers: set[tuple[str, str]] = set()
    for tier_set in list_product_tiers(db, window.policy_year_id).values():
        if tier_set.product_id not in product_ids:
            continue
        ages = ages_by_product.get(tier_set.product_id) or {None}
        for tier in tier_set.tiers:
            if any(
                member_price_tag(
                    source_map=source_map,
                    rule=FlexDrawdownRule.full,
                    pricing=pricing,
                    slip_idx=slip_index,
                    product_id=tier_set.product_id,
                    age=age,
                    declined=False,
                    tier_category_id=tier.tier_category_id,
                    plan_code=tier.plan_code,
                    default_tier_category_id=tier_set.baseline_tier_category_id,
                    default_plan=tier_set.baseline_plan_code,
                )
                is None
                for age in ages
            ):
                missing_tiers.add((tier_set.product_code, tier.label))
    if missing_tiers:
        issues.append(
            _issue(
                "flex_prices_incomplete",
                "Some electable tiers have no resolvable per-member price for the "
                "employees who can receive them.",
                count=len(missing_tiers),
                products={code for code, _label in missing_tiers},
            )
        )
    return issues
