"""Location-scoped catch-alls and separately priced location cohorts."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Product
from app.services.eligibility_mapping.base import (
    _BASED_IN_RE,
    AttributeValueCatalog,
    Rule,
    RuleProposal,
)
from app.services.eligibility_mapping.clauses import _rule_for_values
from app.services.eligibility_mapping.predicates import is_employee_mapping_category
from app.services.eligibility_mapping.vocabulary import _intent_text, _location_cohort_label
from app.services.eligibility_scope import (
    is_catch_all,
    location_cohort_value,
    location_scope,
)
from app.services.explicit_grade_clauses import has_explicit_grade_clause
from app.services.matching_engine import (
    category_entity_gate,
    entity_alias_map,
    product_entities,
)


def _scope_catch_all(
    proposal: RuleProposal, category: Category, catalog: AttributeValueCatalog
) -> RuleProposal:
    """Narrow "All Employees — Thai Office" to the staff at that office."""
    scope = location_scope(category) or ""
    attr, value = location_cohort_value(scope, catalog.values)
    if attr and value is not None:
        return RuleProposal(
            rule=(
                {"=": [attr, value]}
                if is_catch_all(proposal.rule)
                else {"and": [proposal.rule, {"=": [attr, value]}]}
                if proposal.rule
                else None
            ),
            human_readable=f"{proposal.human_readable}; {attr} is {value}",
            confidence=proposal.confidence,
            source=proposal.source,
            validation_state=proposal.validation_state,
            unresolved_clauses=proposal.unresolved_clauses,
            referenced_attributes=list(dict.fromkeys([*proposal.referenced_attributes, attr])),
        )
    return RuleProposal(
        rule=None,
        human_readable=f"{scope} staff need a location field mapping",
        confidence=0.0,
        source="unmapped",
        validation_state="needs_review",
        unresolved_clauses=[scope],
    )


def _separate_location_cohorts(
    categories: list[Category],
    catalog: AttributeValueCatalog,
    *,
    target_category: Category | None = None,
    product_gate: frozenset[str] = frozenset(),
    aliases: dict[str, frozenset[str]] | None = None,
) -> dict[str | None, dict[str, list[Any]]]:
    """Find location cohorts the slip prices separately from grade cohorts."""
    by_product: dict[str | None, dict[str, list[Any]]] = defaultdict(dict)
    for category in categories:
        if target_category is not None:
            if category.product_id != target_category.product_id:
                continue
            target_gate = category_entity_gate(target_category, product_gate, aliases)
            location_gate = category_entity_gate(category, product_gate, aliases)
            if target_gate and location_gate and not target_gate & location_gate:
                continue
        text = _intent_text(category.raw_description)
        if has_explicit_grade_clause(text):
            continue
        attribute_id, value = _location_cohort_label(text, catalog)
        if attribute_id and value is not None:
            values = by_product[category.product_id].setdefault(attribute_id, [])
            if value not in values:
                values.append(value)
    return by_product


def location_exclusions_for_category(
    db: Session,
    *,
    category: Category,
    catalog: AttributeValueCatalog,
    client_id: str,
) -> dict[str, list[Any]]:
    """Find separately priced location labels that can cover this category's entities."""
    categories = list(db.execute(
        select(Category).where(Category.policy_year_id == category.policy_year_id)
    ).scalars())
    if category not in categories:
        categories.append(category)
    aliases = entity_alias_map(db, client_id)
    product = db.get(Product, category.product_id) if category.product_id else None
    cohorts = _separate_location_cohorts(
        [item for item in categories if is_employee_mapping_category(item)],
        catalog,
        target_category=category,
        product_gate=product_entities(product, aliases),
        aliases=aliases,
    )
    return cohorts.get(category.product_id, {})


def _location_exclusion_guard(attribute_id: str, values: list[Any]) -> Rule:
    # evaluate() treats a missing attribute as false for `in`, so its negation
    # preserves grade matches when the roster category is blank.
    return {"not": {"in": [attribute_id, values]}}


def _exclude_separate_location_cohorts(
    proposal: RuleProposal,
    category: Category,
    exclusions: dict[str, list[Any]],
) -> RuleProposal:
    """Keep grade-only rows from swallowing a separately priced location cohort."""
    if (
        proposal.rule is None
        or not has_explicit_grade_clause(category.raw_description)
        or _BASED_IN_RE.search(category.raw_description)
    ):
        return proposal
    if not exclusions:
        return proposal
    guards = [
        _location_exclusion_guard(attribute_id, values)
        for attribute_id, values in exclusions.items()
    ]
    if (
        isinstance(proposal.rule, dict)
        and isinstance(proposal.rule.get("and"), list)
        and proposal.rule["and"][-len(guards):] == guards
    ):
        return proposal
    return replace(
        proposal,
        rule={"and": [proposal.rule, *guards]},
        human_readable=f"{proposal.human_readable}; excluding separately priced location cohort",
        referenced_attributes=list(
            dict.fromkeys([*proposal.referenced_attributes, *exclusions])
        ),
    )


def _rule_without_product_location_context(
    rule: Rule | None, validation: dict[str, Any]
) -> Rule | None:
    """Store a reusable cohort rule without exclusions required by one product."""
    scope_guard = validation.get("location_scope_rule")
    if isinstance(scope_guard, dict) and isinstance(rule, dict):
        if rule == scope_guard:
            rule = {"and": []}
        elif isinstance(parts := rule.get("and"), list) and parts and parts[-1] == scope_guard:
            rule = parts[0] if len(parts) == 2 else {"and": parts[:-1]}
    exclusions = validation.get("product_location_exclusions")
    if not isinstance(exclusions, dict) or not isinstance(rule, dict):
        return rule
    guards = [
        _location_exclusion_guard(attribute_id, values)
        for attribute_id, values in exclusions.items()
        if isinstance(attribute_id, str) and isinstance(values, list) and values
    ]
    legacy_guards = [
        _rule_for_values(attribute_id, values, negate=True)
        for attribute_id, values in exclusions.items()
        if isinstance(attribute_id, str) and isinstance(values, list) and values
    ]
    parts = rule.get("and")
    if (
        not guards
        or not isinstance(parts, list)
        or (parts[-len(guards):] != guards and parts[-len(legacy_guards):] != legacy_guards)
    ):
        return rule
    base = parts[:-len(guards)]
    return base[0] if len(base) == 1 else {"and": base} if base else rule
