"""Employee-scope, bulk-confirm and card-placeholder category predicates."""

from __future__ import annotations

import re
from typing import Any

from app.models import Category
from app.models.category import CategoryStatus

# Categories added from the cards have no slip wording: their name is the cohort
# wording that grouping, overlaps, profile reuse and AI suggest key on.
CARD_SOURCE_REF = "category_card"
# The cards name a new category "New employee category" (then " 2", " 3" within
# a product) until the broker renames it. That placeholder describes no cohort.
_PLACEHOLDER_CARD_NAME_RE = re.compile(r"new employee category(?: \d+)?", re.IGNORECASE)


def is_placeholder_card_category(category: Category) -> bool:
    """A card category still named by its placeholder: its wording must never
    key a reusable mapping shared across products and policy years."""

    return category.source_ref == CARD_SOURCE_REF and bool(
        _PLACEHOLDER_CARD_NAME_RE.fullmatch((category.raw_description or "").strip())
    )


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
