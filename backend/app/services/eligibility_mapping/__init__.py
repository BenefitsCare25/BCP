"""Company-aware eligibility intent extraction and rule validation.

The legacy :mod:`rule_generator` translates one description in isolation using
global regexes.  This package deliberately takes the opposite approach: it maps
the slip's words onto the *current company's* populated employee attributes and
keeps unresolved intent explicit instead of silently dropping clauses.

The pure proposal and validation modules (``vocabulary``, ``clauses``,
``proposal``, ``validation``) are intentionally easy to exercise as golden
evals; database orchestration lives in the remaining modules.
"""

from __future__ import annotations

from app.services.eligibility_mapping.ai_context import build_ai_eligibility_inputs
from app.services.eligibility_mapping.assessment import _rule_messages as _rule_messages
from app.services.eligibility_mapping.assessment import _rule_status as _rule_status
from app.services.eligibility_mapping.assessment import assess_category_rule
from app.services.eligibility_mapping.assignment import (
    _assignment_counts as _assignment_counts,
)
from app.services.eligibility_mapping.assignment import current_category_overlaps
from app.services.eligibility_mapping.auto_map import auto_map_policy_year
from app.services.eligibility_mapping.base import (
    LISTING_RULE_SOURCE,
    AttributeValueCatalog,
    CategoryRuleAssessment,
    MappingItem,
    MappingSummary,
    MissingCategoryPlan,
    Rule,
    RuleProposal,
    RuleValidation,
)
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.confirmation import (
    CategoryConfirmationBatch,
    confirm_category_mapping,
)
from app.services.eligibility_mapping.location_cohorts import (
    _exclude_separate_location_cohorts as _exclude_separate_location_cohorts,
)
from app.services.eligibility_mapping.location_cohorts import (
    _rule_without_product_location_context as _rule_without_product_location_context,
)
from app.services.eligibility_mapping.location_cohorts import (
    _separate_location_cohorts as _separate_location_cohorts,
)
from app.services.eligibility_mapping.location_cohorts import location_exclusions_for_category
from app.services.eligibility_mapping.predicates import (
    BULK_CONFIRM_MIN_CONFIDENCE,
    bulk_confirm_filters,
    is_bulk_confirmable,
    is_employee_mapping_category,
    is_employee_scope,
)
from app.services.eligibility_mapping.proposal import propose_category_rule
from app.services.eligibility_mapping.summary import (
    missing_category_plans,
    stored_mapping_summary,
)
from app.services.eligibility_mapping.validation import (
    normalize_ai_matching_rule,
    validate_ai_matching_rule,
    validate_matching_rule,
)
from app.services.eligibility_mapping.vocabulary import category_signature

__all__ = [
    "BULK_CONFIRM_MIN_CONFIDENCE",
    "LISTING_RULE_SOURCE",
    "AttributeValueCatalog",
    "CategoryConfirmationBatch",
    "CategoryRuleAssessment",
    "MappingItem",
    "MappingSummary",
    "MissingCategoryPlan",
    "Rule",
    "RuleProposal",
    "RuleValidation",
    "assess_category_rule",
    "auto_map_policy_year",
    "build_ai_eligibility_inputs",
    "build_attribute_catalog",
    "bulk_confirm_filters",
    "category_signature",
    "confirm_category_mapping",
    "current_category_overlaps",
    "is_bulk_confirmable",
    "is_employee_mapping_category",
    "is_employee_scope",
    "location_exclusions_for_category",
    "missing_category_plans",
    "normalize_ai_matching_rule",
    "propose_category_rule",
    "stored_mapping_summary",
    "validate_ai_matching_rule",
    "validate_matching_rule",
]
