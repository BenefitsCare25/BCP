"""Employee-scope and bulk-confirm category predicates."""

from __future__ import annotations

from typing import Any

from app.models import Category
from app.models.category import CategoryStatus


def is_employee_scope(plan_assignments: Any) -> bool:
    """Dependant-only price/option rows do not assign an employee cohort."""

    assignments = plan_assignments if isinstance(plan_assignments, dict) else {}
    return str(assignments.get("member_scope") or "employee").casefold() != "dependant"


def is_employee_mapping_category(category: Category) -> bool:
    return is_employee_scope(category.plan_assignments)


# The bulk-confirm endpoint and the "N validated rules" count in the benefit-year
# panel must select the same rows; both read this predicate.
BULK_CONFIRM_MIN_CONFIDENCE = 0.85


def bulk_confirm_filters(min_confidence: float = BULK_CONFIRM_MIN_CONFIDENCE) -> tuple[Any, ...]:
    return (
        Category.status == CategoryStatus.needs_review.value,
        Category.rule_status == "validated",
        Category.matching_rule.is_not(None),
        Category.confidence.is_not(None),
        Category.confidence >= min_confidence,
    )


def is_bulk_confirmable(category: Category) -> bool:
    return (
        category.status == CategoryStatus.needs_review.value
        and category.rule_status == "validated"
        and category.matching_rule is not None
        and category.confidence is not None
        and category.confidence >= BULK_CONFIRM_MIN_CONFIDENCE
        and is_employee_mapping_category(category)
    )
