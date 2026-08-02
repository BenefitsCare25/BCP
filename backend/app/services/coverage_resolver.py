"""Resolve effective per-employee coverage = category default + sparse overrides.

The cohort default for an employee's plan is their matched
``Category.plan_assignments[product]``. An ``EmployeePlanOverride`` (written by an
enrollment confirmation, a bulk update, or a manual admin edit) deviates from
that default for one (employee, product) pair.

This module is the single place that merges the two so every read path
(``plan_hydration`` → benefit statement, employee coverage-summary, exports)
resolves coverage identically. Keep override-application logic here, not inlined
in callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, EmployeePlanOverride


@dataclass(frozen=True)
class ResolvedPlan:
    """Effective plan for an (employee, product) after applying any override."""

    plan_code: str | None
    declined: bool
    overridden: bool
    override_source: str | None
    covered_dependant_ids: list[str] | None


def load_overrides(
    db: Session, policy_year_id: str, employee_ids: list[str]
) -> dict[tuple[str, str], EmployeePlanOverride]:
    """Return ``{(employee_id, product_id): override}`` for the given employees.

    Scoped to one policy year so overrides never bleed across years.
    """
    if not employee_ids:
        return {}
    rows = (
        db.execute(
            select(EmployeePlanOverride).where(
                EmployeePlanOverride.policy_year_id == policy_year_id,
                EmployeePlanOverride.employee_id.in_(employee_ids),
            )
        )
        .scalars()
        .all()
    )
    return {(o.employee_id, o.product_id): o for o in rows}


def resolve_plan(
    override: EmployeePlanOverride | None, category_plan_code: str | None
) -> ResolvedPlan:
    """Merge a single override (if any) over the cohort default plan code."""
    if override is None:
        return ResolvedPlan(
            plan_code=category_plan_code,
            declined=False,
            overridden=False,
            override_source=None,
            covered_dependant_ids=None,
        )
    if override.declined:
        return ResolvedPlan(
            plan_code=None,
            declined=True,
            overridden=True,
            override_source=override.source,
            covered_dependant_ids=None,
        )
    return ResolvedPlan(
        # An override with no explicit plan_code keeps the cohort default plan but
        # may still carry dependant-coverage changes.
        plan_code=override.plan_code or category_plan_code,
        declined=False,
        overridden=True,
        override_source=override.source,
        covered_dependant_ids=override.covered_dependant_ids,
    )


def is_sparse_default(
    *,
    declined: bool,
    plan_code: str | None,
    tier_category_id: str | None,
    covered_dependant_ids: list[str] | None,
    default_plan: str | None,
    base_tier: str | None,
    dependant_option_ids: dict[str, Any] | None = None,
) -> bool:
    """Does this coverage state equal the member's cohort default (→ no override)?

    The single source of truth for the sparse-storage rule shared by enrollment
    projection, revert-to-baseline, and any future writer: an override is needed
    ONLY when the member deviates from their matched-category default. A deviation
    is a decline, a different plan, a different tier, any explicit dependant
    selection — including an explicit empty list (``[]`` means "cover no
    dependants", which differs from the default and must persist); only ``None``
    (no dependant opinion) counts as default — OR an elected dependant option
    level (``dependant_option_ids``).
    """
    return (
        not declined
        and (plan_code or default_plan) == default_plan
        and tier_category_id in (None, base_tier)
        and covered_dependant_ids is None
        and not dependant_option_ids
    )


def batch_category_defaults(
    db: Session, employees: list[Employee]
) -> dict[str, dict[str, tuple[str, str | None]]]:
    """``{employee_id: {product_id: (product_code, default_plan_code)}}`` in one query.

    The cohort default comes from each matched category's product +
    ``plan_assignments['plan_code']``. Batched so bulk paths don't issue a
    Category↔Product join per employee.
    """
    from app.models.category import Category  # local import avoids a cycle
    from app.models.product import Product

    cat_to_emps: dict[str, list[str]] = {}
    for emp in employees:
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            if cid:
                cat_to_emps.setdefault(cid, []).append(emp.id)
    out: dict[str, dict[str, tuple[str, str | None]]] = {e.id: {} for e in employees}
    if not cat_to_emps:
        return out
    rows = db.execute(
        select(Category.id, Category.product_id, Product.code, Category.plan_assignments)
        .join(Product, Category.product_id == Product.id)
        .where(Category.id.in_(cat_to_emps.keys()), Category.product_id.is_not(None))
    ).all()
    for cat_id, product_id, code, pa in rows:
        plan_code = (pa or {}).get("plan_code") if isinstance(pa, dict) else None
        for emp_id in cat_to_emps.get(cat_id, []):
            out[emp_id][product_id] = (code, plan_code)
    return out


def employee_category_defaults(
    db: Session, employee: Employee
) -> dict[str, tuple[str, str | None]]:
    """Map ``product_id -> (product_code, default_plan_code)`` for one employee.

    Single-employee convenience wrapper over :func:`batch_category_defaults`.
    """
    return batch_category_defaults(db, [employee]).get(employee.id, {})


def employee_compulsory_product_ids(db: Session, employee: Employee) -> set[str]:
    """Product IDs where this employee's matched category has participation_model='compulsory'.

    Used by enrollment enforcement to block decline elections and to skip
    compulsory products when applying the deemed-decline default at window close.
    """
    from app.models.category import Category  # local import avoids cycle

    cat_ids = [
        m["category_id"]
        for m in (employee.matched_categories or [])
        if m.get("category_id")
    ]
    if not cat_ids:
        return set()
    rows = db.execute(
        select(Category.product_id).where(
            Category.id.in_(cat_ids),
            Category.participation_model == "compulsory",
            Category.product_id.is_not(None),
        )
    ).scalars().all()
    return set(rows)


def find_orphan_overrides(
    db: Session, policy_year_id: str, employees: list[Employee]
) -> list[EmployeePlanOverride]:
    """Overrides stranded by category churn, in either of two ways:

    1. The override's PRODUCT is no longer covered by the employee's current
       matched categories (re-matching moved them to a different cohort).
    2. The override's elected ``tier_category_id`` no longer resolves to a live
       Category row (a slip re-parse deleted the tier) — the coverage line
       still renders, but its pricing silently falls back to the baseline
       tier, so the election must be reconciled.

    Surface both so they can be reconciled rather than silently applied,
    repriced, or dropped.
    """
    emp_ids = [e.id for e in employees]
    overrides = load_overrides(db, policy_year_id, emp_ids)
    if not overrides:
        return []

    # Product ids each employee currently has a matched category for.
    from app.models.category import Category  # local import avoids a cycle

    cat_ids: set[str] = set()
    for e in employees:
        for m in e.matched_categories or []:
            if m.get("category_id"):
                cat_ids.add(m["category_id"])
    cat_product: dict[str, str | None] = {}
    if cat_ids:
        cat_product = dict(
            db.execute(
                select(Category.id, Category.product_id).where(Category.id.in_(cat_ids))
            ).all()
        )

    emp_product_ids: dict[str, set[str]] = {}
    for e in employees:
        prods: set[str] = set()
        for m in e.matched_categories or []:
            pid = cat_product.get(m.get("category_id") or "")
            if pid:
                prods.add(pid)
        emp_product_ids[e.id] = prods

    # Liveness of every elected tier category referenced by an override
    # (tier_category_id is deliberately NOT an FK — see the model).
    tier_ids = {ov.tier_category_id for ov in overrides.values() if ov.tier_category_id}
    live_tier_ids: set[str] = set()
    if tier_ids:
        live_tier_ids = set(
            db.execute(
                select(Category.id).where(Category.id.in_(tier_ids))
            ).scalars()
        )

    orphans: list[EmployeePlanOverride] = []
    for (emp_id, product_id), ov in overrides.items():
        if product_id not in emp_product_ids.get(emp_id, set()):
            orphans.append(ov)
        elif ov.tier_category_id and ov.tier_category_id not in live_tier_ids:
            orphans.append(ov)
    return orphans
