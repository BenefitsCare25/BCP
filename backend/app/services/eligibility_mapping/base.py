"""Shared rule type, vocabulary constants, and result dataclasses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

Rule = dict[str, Any]

# Source marker for rules learned from a company's Employee Listing
# (``services/el_import/joiner_rules``); such rules survive auto-mapping.
LISTING_RULE_SOURCE = "employee_listing"

_PLAN_PREFIX_RE = re.compile(r"^\s*plan\s+[a-z0-9/]+\s*(?:-|:|\u2013|\u2014)\s*", re.IGNORECASE)
_DEPENDANT_TAIL_RE = re.compile(
    r"\s+(?:and|&)\s+(?:(?:their|the)\s+)?(?:eligible\s+)?"
    r"depend[ae]n[td]s?.*$",
    re.IGNORECASE,
)
_OPTION_TAIL_RE = re.compile(r"\s*\(option\s+\d+\)\s*$", re.IGNORECASE)
_EXCLUSION_RE = re.compile(
    r"\b(?:excluding|excl\.?|except(?:\s+for)?)\s+([^)]*)(?:\)|$)", re.IGNORECASE
)
_BASED_IN_RE = re.compile(
    r"\bbased\s+in\s+([^()]+?)(?=\s*(?:\(\s*)?(?:excluding|except)\b|\s*\(|$)",
    re.IGNORECASE,
)
_LOCATION_ALIAS_SEPARATOR_RE = re.compile(r"\s*(?:,|/|;|&|\band\b)\s*", re.IGNORECASE)
_ALL_EMPLOYEES_RE = re.compile(r"^all\s+(?:employees?|staff|members?)$", re.IGNORECASE)
_ALL_OTHER_RE = re.compile(r"^all\s+others?\s+(?:employees?|staff|members?)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TOKEN_ALIASES = {
    "asst": "assistant",
    "dept": "department",
    "mgr": "manager",
}

_ATTR_PRIORITY = (
    "designation",
    "employee_category",
    "employment_type",
    "employee_type",
    "job_title",
    "category",
    "role",
    "job_grade",
    "job_category",
    "class",
    "occupation",
    "job_function",
)
_PASS_ATTRS = ("pass", "pass_type", "employment_pass", "work_pass")
_WORK_LOCATION_ATTRS = (
    "country_of_work",
    "work_country",
    "work_location",
    "location",
    "location_description",
    "office_location",
    "country",
)
_NATIONALITY_ATTRS = (
    "nationality",
    "nationality_code",
    "citizenship",
    "country_of_citizenship",
)
_COST_CENTRE_ATTRS = (
    "cost_centre",
    "cost_center",
    "cost_centre_code",
    "cost_center_code",
)
_NATIONALITY_RE = re.compile(
    r"\b(?:nationality|citizenship|nationals?|citizens?)\b",
    re.IGNORECASE,
)
_COST_CENTRE_RE = re.compile(
    r"\bcost\s*cent(?:re|er)(?:\s+code)?\b",
    re.IGNORECASE,
)
_LEAF_OPS = frozenset({"=", "==", "!=", ">=", "<=", ">", "<", "between", "in", "not_in"})
_MAX_RULE_DEPTH = 12
_MAX_RULE_NODES = 100
_MAX_SET_VALUES = 200


@dataclass(frozen=True)
class AttributeValueCatalog:
    """Non-PII employee attribute vocabulary available to the compiler.

    ``values`` contains actual distinct roster values when a roster exists, or
    configured enum values when it does not. ``populated`` always represents
    real roster fill counts, never enum cardinality.
    """

    values: dict[str, list[Any]]
    data_types: dict[str, str]
    populated: dict[str, int]
    employee_count: int
    roster_present: bool = True
    # Configured enums remain valid even when the current roster has no member
    # in that band yet (for example, a future M1 hire). Keeping them separate
    # from observed values lets validation distinguish "allowed but unused"
    # from an AI-invented literal.
    configured_values: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def attribute_ids(self) -> set[str]:
        return set(self.data_types) | set(self.values) | set(self.populated)


@dataclass(frozen=True)
class RuleProposal:
    rule: Rule | None
    human_readable: str
    confidence: float
    source: str
    validation_state: str
    unresolved_clauses: list[str] = field(default_factory=list)
    referenced_attributes: list[str] = field(default_factory=list)
    relative_remainder: bool = False


@dataclass(frozen=True)
class RuleValidation:
    valid: bool
    errors: list[str]
    warnings: list[str]
    referenced_attributes: list[str]


@dataclass(frozen=True)
class MappingItem:
    category_id: str
    product_code: str | None
    display_name: str
    plan_code: str | None
    category_status: str
    rule_status: str
    source: str
    matching_rule: Rule | None
    rule_human_readable: str | None
    confidence: float | None
    matched_count: int | None
    expected_count: int | None
    unresolved_clauses: list[str]
    errors: list[str]
    warnings: list[str]
    reused: bool
    bulk_confirmable: bool = False


@dataclass(frozen=True)
class MissingCategoryPlan:
    plan_id: str
    product_id: str
    product_code: str
    product_display_name: str
    plan_code: str
    plan_display_name: str
    source_hint: str | None


@dataclass(frozen=True)
class CategoryRuleAssessment:
    valid: bool
    rule_status: str
    validation: dict[str, Any]


@dataclass(frozen=True)
class MappingSummary:
    policy_year_id: str
    employee_count: int
    total: int
    validated: int
    proposed: int
    needs_review: int
    unmapped: int
    not_applicable: int
    reused: int
    categories: list[MappingItem]
    missing_categories: int
    missing_category_plans: list[MissingCategoryPlan]
