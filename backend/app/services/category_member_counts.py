"""Live per-category member counts, resolved from the roster.

A ``Category``'s ``plan_assignments`` carries the headcount the INSURER'S SLIP
stated when it was parsed. That figure is a point-in-time quote input: it never
moves again, while the roster does (hires, leavers, ADC movements). Any document
that goes back out to an insurer needs the roster's answer, not the slip's — the
same way the fact-find form fills its member tables.

This module is the single place that answers "who is in this category right
now". It resolves effective coverage through ``coverage_resolver`` (category
default + sparse override), exactly like the fact-find and the benefit
statement, so a declined member is dropped and an enrollment-elected dependant
subset binds. Counting ``matched_categories`` directly would ignore both.

Read-only and product-agnostic: nothing here knows any product code. Whether a
category reports dependants and a per-tier split is decided by the product's own
``has_dependants`` flag and the household's composition.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Dependant, Employee, Product
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.services.dependant_coverage import category_covers_dependants
from app.services.coverage_resolver import load_overrides, resolve_plan
from app.services.plan_hydration import resolve_basis_amount
from app.services.roster_attributes import family_tier_bucket

DEPENDANT_STATUS_ACTIVE = "active"


@dataclass(frozen=True)
class CategoryMembers:
    """Who the roster currently places in one category."""

    employees: int = 0
    dependants: int = 0
    # Composite tier split (EO/ES/EC/EF) of the employees above — only populated
    # for products that cover dependants, where the split is what prices the
    # cover. Keys are the canonical tier codes from the product registry.
    tier_counts: dict[str, int] = field(default_factory=dict)
    # Group sum insured summed across these members, for a category whose basis
    # is SALARY-RELATIVE ("12 times basic monthly salary"). Such a basis has no
    # per-member amount to multiply by a headcount, so the aggregate can only be
    # built here, member by member. None when the basis is a plain amount (the
    # caller multiplies) or unresolvable (relative to another product, or no
    # salary on file).
    sum_insured: float | None = None


def build_category_member_counts(
    db: Session, policy_year_id: str
) -> dict[str, CategoryMembers]:
    """Map category id → live membership for every category of a policy year.

    Categories nobody matches are simply absent from the result (an explicit
    zero and "never computed" are the same thing to callers, which must decide
    their own fallback — see ``slip_export`` for the one that keeps the slip's
    stated figure rather than publishing a zero).
    """
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year_id,
                Employee.status == EMPLOYEE_STATUS_ACTIVE,
            )
        ).scalars()
    )
    if not employees:
        return {}

    deps_by_emp: dict[str, list[Dependant]] = defaultdict(list)
    for dep in db.execute(
        select(Dependant).where(
            Dependant.policy_year_id == policy_year_id,
            Dependant.status == DEPENDANT_STATUS_ACTIVE,
        )
    ).scalars():
        if dep.employee_id:
            deps_by_emp[dep.employee_id].append(dep)

    # product_id per category, plus which products cover dependants at all.
    cat_product: dict[str, str | None] = {}
    covers_dependants: dict[str, bool] = {}
    cat_assignments: dict[str, dict[str, Any]] = {}
    for cid, product_id, assignments, has_deps, detail, display_name, raw in db.execute(
        select(
            Category.id,
            Category.product_id,
            Category.plan_assignments,
            Product.has_dependants,
            Category.participation_detail,
            Category.display_name,
            Category.raw_description,
        )
        .outerjoin(Product, Category.product_id == Product.id)
        .where(Category.policy_year_id == policy_year_id)
    ).all():
        cat_product[cid] = product_id
        cat_assignments[cid] = assignments if isinstance(assignments, dict) else {}
        covers_dependants[cid] = category_covers_dependants(
            bool(has_deps),
            cat_assignments[cid],
            detail if isinstance(detail, dict) else None,
            display_name,
            raw,
        )

    overrides = load_overrides(db, policy_year_id, [e.id for e in employees])

    emp_counts: dict[str, int] = defaultdict(int)
    dep_counts: dict[str, int] = defaultdict(int)
    tier_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    basis_totals: dict[str, float] = defaultdict(float)
    basis_missing: set[str] = set()

    for emp in employees:
        household = deps_by_emp.get(emp.id, [])
        # A member can match several categories (one per product); each is a
        # separate cover line and counts once in its own category.
        for match in emp.matched_categories or []:
            cid = match.get("category_id")
            if not cid or cid not in cat_product:
                # Dangling entry — the category was deleted by a re-parse. Same
                # treatment as hydrate_plans: skip rather than invent a row.
                continue
            product_id = cat_product[cid]
            override = overrides.get((emp.id, product_id)) if product_id else None
            # A declined override means the member opted out — no cover line, so
            # they are not in this category's count either.
            if resolve_plan(override, None).declined:
                continue
            emp_counts[cid] += 1
            # Accumulate the member's own sum assured. A single member missing a
            # salary makes the AGGREGATE wrong rather than merely incomplete, so
            # the category is marked and reports no roster-derived cover at all.
            own = resolve_basis_amount(cat_assignments.get(cid, {}), emp.attribute_values)
            if own is None:
                basis_missing.add(cid)
            else:
                basis_totals[cid] += own
            if not covers_dependants.get(cid):
                continue
            covered = household
            if override is not None and override.covered_dependant_ids is not None:
                chosen = set(override.covered_dependant_ids)
                covered = [d for d in household if d.id in chosen]
            dep_counts[cid] += len(covered)
            bucket = family_tier_bucket(d.attribute_values or {} for d in covered)
            tier_counts[cid][bucket] += 1

    return {
        cid: CategoryMembers(
            employees=count,
            dependants=dep_counts.get(cid, 0),
            tier_counts=dict(tier_counts.get(cid, {})),
            sum_insured=(
                round(basis_totals[cid], 2)
                if cid not in basis_missing and basis_totals.get(cid)
                else None
            ),
        )
        for cid, count in emp_counts.items()
    }
