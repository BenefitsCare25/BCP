"""Rule status, warning messages, and single-category rule assessment."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category
from app.services.eligibility_mapping.assignment import _assignment_counts
from app.services.eligibility_mapping.base import (
    AttributeValueCatalog,
    CategoryRuleAssessment,
    Rule,
    RuleValidation,
)
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.location_cohorts import location_exclusions_for_category
from app.services.eligibility_mapping.validation import validate_ai_matching_rule


def _rule_messages(
    *,
    catalog: AttributeValueCatalog,
    matching_rule: Rule | None,
    validation: RuleValidation,
    matched: int | None,
    expected: int | None,
    overlap_count: int,
    unresolved: list[str],
) -> tuple[list[str], list[str]]:
    """Return display warnings and the subset that blocks auto-validation."""

    warnings = list(validation.warnings)
    blockers: list[str] = []
    if not catalog.roster_present and matching_rule is not None:
        warnings.append("No active employee listing is available to validate matched employees")
    if overlap_count:
        noun = "employee" if overlap_count == 1 else "employees"
        verb = "matches" if overlap_count == 1 else "match"
        message = f"{overlap_count} {noun} also {verb} an equally specific employee cohort"
        warnings.append(message)
        blockers.append(message)
    if expected is not None and matched is not None and expected != matched:
        warnings.append(f"Matched {matched} employees; placement slip states {expected}")
    if matched == 0 and expected and matching_rule is not None:
        message = "Rule matches no active employees although the slip states a headcount"
        warnings.append(message)
        blockers.append(message)
    for clause in unresolved:
        message = f"Unresolved clause: {clause}"
        warnings.append(message)
        blockers.append(message)
    return list(dict.fromkeys(warnings)), list(dict.fromkeys(blockers))


def _rule_status(
    *,
    matching_rule: Rule | None,
    validation: RuleValidation,
    blockers: list[str],
    roster_present: bool,
) -> str:
    if matching_rule is None:
        return "unmapped"
    if validation.errors or blockers:
        return "needs_review"
    return "validated" if roster_present else "proposed"


def assess_category_rule(
    db: Session,
    *,
    category: Category,
    client_id: str,
    unresolved_clauses: list[str] | None = None,
    source: str,
) -> CategoryRuleAssessment:
    """Validate one proposed rule and measure its real roster outcome.

    This is the save gate for AI output. It uses the same evaluation and
    precedence as the live matcher, so a syntactically plausible rule cannot be
    presented as ready when it references invented values, matches nobody, or
    conflicts with an equally-specific sibling.
    """

    catalog, employees, views = build_attribute_catalog(db, category.policy_year_id, client_id)
    categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == category.policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    if category not in categories:
        categories.append(category)
    location_exclusions = location_exclusions_for_category(
        db, category=category, catalog=catalog, client_id=client_id
    )
    validation = validate_ai_matching_rule(
        category.raw_description,
        category.matching_rule,
        catalog,
        location_exclusions=location_exclusions,
    )
    counts, overlaps = _assignment_counts(
        db=db,
        client_id=client_id,
        categories=categories,
        employees=employees,
        views=views,
        excluded_category_ids={category.id} if not validation.valid else None,
        validated_category_ids={category.id} if validation.valid else None,
    )

    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    expected_raw = pa.get("num_employees")
    expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
    matched = counts.get(category.id, 0) if catalog.roster_present else None
    unresolved = [str(value)[:512] for value in (unresolved_clauses or [])][:20]
    warnings, blockers = _rule_messages(
        catalog=catalog,
        matching_rule=category.matching_rule,
        validation=validation,
        matched=matched,
        expected=expected,
        overlap_count=overlaps.get(category.id, 0),
        unresolved=unresolved,
    )
    rule_status = _rule_status(
        matching_rule=category.matching_rule,
        validation=validation,
        blockers=blockers,
        roster_present=catalog.roster_present,
    )
    payload = {
        "state": rule_status,
        "source": source,
        "errors": validation.errors,
        "warnings": warnings,
        "overlap_count": overlaps.get(category.id, 0),
        "unresolved_clauses": unresolved,
        "required_attributes": validation.referenced_attributes,
        "matched_count": matched,
        "expected_count": expected,
        "reused": False,
    }
    return CategoryRuleAssessment(
        valid=validation.valid,
        rule_status=rule_status,
        validation=payload,
    )
