"""Roster assignment counts and overlap detection with live-matcher precedence."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Employee, Product
from app.models.category import CategoryStatus
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.vocabulary import category_signature
from app.services.eligibility_scope import location_allows
from app.services.matching_engine import (
    _entity_allows,
    category_entity_gate,
    category_specificity,
    employee_entity,
    entity_alias_map,
    product_entities,
    rule_has_validation_errors,
)
from app.services.rule_evaluator import evaluate


def _assignment_counts(
    *,
    db: Session,
    client_id: str,
    categories: list[Category],
    employees: list[Employee],
    views: list[dict[str, Any]],
    overlap_details: dict[str, list[dict[str, Any]]] | None = None,
    excluded_category_ids: set[str] | None = None,
    validated_category_ids: set[str] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Count matched eligibility cohorts with the live matcher's precedence.

    Alternative plan/tier rows can legitimately share one cohort rule. Collapse
    those siblings by company-scoped category signature before diagnosing an
    ambiguity, and give each sibling the cohort count shown in the workbench.
    Exact/fuzzy matching remains excluded so it cannot validate a broken rule.
    """

    by_product: dict[str | None, list[Category]] = defaultdict(list)
    for category in categories:
        by_product[category.product_id].append(category)

    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    aliases = entity_alias_map(db, client_id)
    counts: dict[str, int] = defaultdict(int)
    overlaps: dict[str, int] = defaultdict(int)
    excluded = excluded_category_ids or set()
    validated = validated_category_ids or set()

    def rank(category: Category) -> tuple[int, int]:
        status_rank = (
            0
            if category.status == CategoryStatus.confirmed.value
            else 1
            if category.status == CategoryStatus.needs_review.value
            else 2
        )
        return status_rank, -category_specificity(category)

    def cohort_identity(category: Category) -> tuple[str, frozenset[str]]:
        return category_signature(category.raw_description), gates[category.id]

    for product_id, product_categories in by_product.items():
        product = products.get(product_id) if product_id else None
        product_gate = product_entities(product, aliases)
        gates = {
            category.id: category_entity_gate(category, product_gate, aliases)
            for category in product_categories
        }
        for employee, view in zip(employees, views, strict=True):
            employee_gate = employee_entity(employee.attribute_values, aliases)
            matches = [
                category
                for category in product_categories
                if category.matching_rule
                and category.id not in excluded
                and (category.id in validated or not rule_has_validation_errors(category))
                and _entity_allows(gates[category.id], employee_gate)
                and location_allows(category, view)
                and evaluate(category.matching_rule, view)
            ]
            if not matches:
                continue
            # Mirror matching_engine.match_one through status + specificity.
            # Priority chooses the baseline tier only after equal-rank rows are
            # collapsed into their employee-cohort meaning.
            best_rank = min(rank(category) for category in matches)
            best = [category for category in matches if rank(category) == best_rank]
            best_cohorts = {cohort_identity(category) for category in best}
            if len(best_cohorts) > 1:
                for category in best:
                    overlaps[category.id] += 1
                    if overlap_details is not None:
                        other_categories = sorted(
                            {
                                other.display_name
                                for other in best
                                if cohort_identity(other) != cohort_identity(category)
                            }
                        )
                        overlap_details.setdefault(category.id, []).append(
                            {
                                "employee_id": employee.id,
                                "staff_id": employee.staff_id,
                                "employee_name": employee.employee_name,
                                "job_category": (
                                    str(view["job_category"])[:64]
                                    if view.get("job_category") is not None
                                    else None
                                ),
                                "other_categories": other_categories,
                            }
                        )
            for category in matches:
                if cohort_identity(category) in best_cohorts:
                    counts[category.id] += 1
    return counts, overlaps


def current_category_overlaps(
    db: Session, *, policy_year_id: str, client_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Identify active employees tied between distinct top-ranked cohorts."""
    _, employees, views = build_attribute_catalog(db, policy_year_id, client_id)
    categories = list(
        db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        ).scalars()
    )
    details: dict[str, list[dict[str, Any]]] = {}
    _assignment_counts(
        db=db,
        client_id=client_id,
        categories=categories,
        employees=employees,
        views=views,
        overlap_details=details,
    )
    return details
