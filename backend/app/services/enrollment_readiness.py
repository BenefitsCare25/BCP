"""Advisory validation for enrolment setup, without preventing opening.

Aggregate issues do not identify employees. Authorized review endpoints provide
affected employees separately, so brokers can review and resolve setup gaps.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Employee,
    FlexScheme,
    MemberAccount,
    PolicyYear,
    Product,
)
from app.models.category import CategoryStatus
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.models.enrollment_window import EnrollmentWindow, FlexDrawdownRule
from app.models.flex_scheme import FlexSchemeStatus
from app.services.cohort_tiers import list_product_tiers
from app.services.coverage_gaps import build_coverage_gaps
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
    severity: str = "warning",
    count_unit: str = "employees",
    employee_count: int | None = None,
) -> dict[str, Any]:
    # Setup validation is advisory; opening remains the broker's decision.
    out: dict[str, Any] = {"code": code, "message": message, "severity": severity}
    if count is not None:
        out["count"] = count
        out["count_unit"] = count_unit
    if employee_count is not None:
        out["employee_count"] = employee_count
    if products:
        out["products"] = sorted(set(products))
    return out


def enrollment_readiness_issues(
    db: Session,
    window: EnrollmentWindow,
    *,
    affected: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Return aggregate validation warnings for ``window``.

    Aggregate output never identifies employees. The authorized review endpoint
    can request a separate collector; open/conflict responses never include it.
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
    # ``product_scope`` holds product CODES everywhere else (close's deemed
    # decline, the elections panel); accept ids too so either spelling scopes.
    scope_codes: dict[str, str] = {}
    if requested_scope is not None:
        for product_id, code in db.execute(
            select(Product.id, Product.code).where(
                Product.id.in_({c.product_id for c in all_categories})
            )
        ).all():
            scope_codes[product_id] = code

    def in_scope(product_id: str | None) -> bool:
        if requested_scope is None:
            return True
        if product_id is None:
            return False
        return product_id in requested_scope or scope_codes.get(product_id) in requested_scope

    categories = [c for c in all_categories if in_scope(c.product_id)]
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
        display_code[key] for key, ids in product_ids_by_code.items() if len(ids) > 1
    }
    if duplicate_codes:
        issues.append(
            _issue(
                "duplicate_product_codes",
                "One benefit code resolves to multiple product records. Reconcile the "
                "product setup.",
                count=len(duplicate_codes),
                products=duplicate_codes,
                count_unit="products",
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
    py = db.get(PolicyYear, window.policy_year_id)
    assert py is not None
    gaps = build_coverage_gaps(db, py)

    def collect(
        code: str,
        employee: Employee,
        *,
        reason: str,
        review_categories: list[Category] | None = None,
        missing_products: set[str] | None = None,
    ) -> None:
        if affected is None:
            return
        attrs = {**(employee.attribute_values or {}), **(employee.derived_attribute_values or {})}
        affected.setdefault(code, []).append(
            {
                "employee_id": employee.id,
                "staff_id": employee.staff_id,
                "employee_name": employee.employee_name,
                "employee_category": str(
                    attrs.get("category") or attrs.get("employee_category") or ""
                ),
                "grade": str(
                    attrs.get("job_category") or attrs.get("job_grade") or attrs.get("grade") or ""
                ),
                "reason": reason,
                "products": sorted(missing_products or set()),
                "mappings": [
                    {
                        "category_id": c.id,
                        "category_name": c.display_name,
                        "product_code": code_by_product.get(c.product_id or "", "Unknown"),
                        "status": c.status,
                    }
                    for c in (review_categories or [])
                ],
            }
        )

    matched_products_by_employee: dict[str, set[str]] = {}
    matched_category_ids: set[str] = set()
    unmatched = 0
    partial_gaps = 0
    missing_codes: set[str] = set()
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
            if isinstance(match, dict) and str(match.get("category_id") or "") in category_product
        )
        matched_products_by_employee[employee.id] = matched
        if not matched:
            unmatched += 1
            collect(
                "employees_without_coverage", employee, reason="No assigned product in this period."
            )
        else:
            missing = {gaps.codes[pid] for pid in (gaps.expected(employee) & product_ids) - matched}
            if missing:
                partial_gaps += 1
                missing_codes.update(missing)
                collect(
                    "employees_with_coverage_gaps",
                    employee,
                    reason="No category assigned for these applicable products.",
                    missing_products=missing,
                )
    if unmatched:
        issues.append(
            _issue(
                "employees_without_coverage",
                "Active employees remain unmatched to every product in scope.",
                count=unmatched,
            )
        )

    if partial_gaps:
        issues.append(
            _issue(
                "employees_with_coverage_gaps",
                "Some active employees have no category for additional products in this "
                "period. Review whether this is intended; employees may have different benefits.",
                count=partial_gaps,
                products=missing_codes,
                severity="warning",
            )
        )

    unconfirmed = [
        category
        for category in categories
        if category.id in matched_category_ids and category.status != CategoryStatus.confirmed.value
    ]
    if unconfirmed:
        unconfirmed_ids = {c.id for c in unconfirmed}
        unconfirmed_employee_count = 0
        for employee in employees:
            assigned = {
                str(m.get("category_id"))
                for m in employee.matched_categories or []
                if isinstance(m, dict)
            }
            review = [c for c in unconfirmed if c.id in assigned]
            if assigned & unconfirmed_ids:
                unconfirmed_employee_count += 1
                collect(
                    "unconfirmed_categories",
                    employee,
                    reason="Assigned eligibility mappings need broker confirmation.",
                    review_categories=review,
                )
        issues.append(
            _issue(
                "unconfirmed_categories",
                "Eligibility mappings currently assigned to employees still require "
                "broker review and confirmation.",
                count=len(unconfirmed),
                count_unit="mappings",
                employee_count=unconfirmed_employee_count,
                products={
                    code_by_product.get(category.product_id or "", "Unknown")
                    for category in unconfirmed
                },
            )
        )

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
                    member_account.status == "invited" and member_account.invite_sent_at is not None
                )
            )
            if not usable:
                inaccessible += 1
                reason = (
                    "No portal account"
                    if member_account is None
                    else "Portal account disabled"
                    if member_account.status == "disabled"
                    else "Invitation not delivered"
                )
                collect("portal_access_incomplete", employee, reason=reason)
        if inaccessible:
            issues.append(
                _issue(
                    "portal_access_incomplete",
                    "Some active employees have no usable portal account or delivered invite.",
                    count=inaccessible,
                    severity="warning",
                )
            )

    if window.member_self_service:
        # The portal's own definition of "the year members see", not a status
        # check: whatever `resolve_member_employee` reads is what must match.
        from app.core.portal_auth import active_policy_year

        live = active_policy_year(db, window.client_id)
        if live is None or live.id != window.policy_year_id:
            issues.append(
                _issue(
                    "benefit_year_not_live",
                    "Members only see the live benefit year in the portal, and this "
                    "one is not live — nobody will see this period until it is made "
                    "current. Make it current, or run the period broker-managed.",
                    severity="warning",
                )
            )

    if not bool(window.uses_flex):
        return issues

    scheme = db.scalar(select(FlexScheme).where(FlexScheme.policy_year_id == window.policy_year_id))
    if scheme is None or scheme.status != FlexSchemeStatus.confirmed:
        issues.append(
            _issue(
                "flex_scheme_not_confirmed",
                "The Flex scheme still requires confirmation.",
            )
        )

    wallets_missing = sum(
        1
        for employee in employees
        if employee.flex_wallet_amount is None or not employee.flex_currency
    )
    if wallets_missing:
        for employee in employees:
            if employee.flex_wallet_amount is None or not employee.flex_currency:
                collect(
                    "flex_wallets_incomplete", employee, reason="Wallet amount or currency missing."
                )
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
                count_unit="tiers",
                products={code for code, _label in missing_tiers},
            )
        )
    return issues
