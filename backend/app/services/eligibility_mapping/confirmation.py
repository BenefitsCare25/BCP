"""Broker confirmation of category mappings, batched across categories."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, EligibilityMappingProfile, Product
from app.models.category import CategoryStatus
from app.services.eligibility_mapping.assessment import _rule_messages
from app.services.eligibility_mapping.base import RuleProposal
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.location_cohorts import (
    _rule_without_product_location_context,
    _separate_location_cohorts,
)
from app.services.eligibility_mapping.predicates import is_employee_mapping_category
from app.services.eligibility_mapping.profiles import _upsert_profile
from app.services.eligibility_mapping.validation import (
    _source_allowed_values,
    validate_ai_matching_rule,
    validate_matching_rule,
)
from app.services.eligibility_mapping.vocabulary import category_signature
from app.services.eligibility_scope import location_allows
from app.services.matching_engine import (
    _entity_allows,
    category_entity_gate,
    category_specificity,
    employee_entity,
    entity_alias_map,
    product_entities,
)
from app.services.rule_evaluator import evaluate

_CohortIdentity = tuple[str, frozenset[str]]


class CategoryConfirmationBatch:
    """Reuse roster matches while confirming several categories in one request.

    Only already-confirmed categories can tie a new confirmed candidate. Rule
    evaluation is cached per category, and each accepted candidate updates the
    best confirmed cohort for its matched employees. This preserves the order
    and result of individual confirmation without rechecking every rule per row.
    """

    def __init__(
        self,
        db: Session,
        *,
        policy_year_id: str,
        client_id: str,
        candidates: list[Category],
    ) -> None:
        self.catalog, self._employees, self._views = build_attribute_catalog(
            db, policy_year_id, client_id
        )
        # Filter in Python so confirmations earlier in the same uncommitted
        # transaction are included even when the session has not flushed yet.
        policy_categories = list(db.execute(
            select(Category).where(Category.policy_year_id == policy_year_id)
        ).scalars())
        mapping_categories = [
            category
            for category in [
                *policy_categories,
                *(candidate for candidate in candidates if candidate not in policy_categories),
            ]
            if is_employee_mapping_category(category)
        ]
        confirmed = [
            category
            for category in policy_categories
            if category.status == CategoryStatus.confirmed.value
        ]
        categories = {category.id: category for category in [*confirmed, *candidates]}
        products = {
            product.id: product
            for product in db.execute(
                select(Product).where(
                    Product.id.in_(
                        {c.product_id for c in categories.values() if c.product_id}
                    )
                )
            ).scalars()
        }
        aliases = entity_alias_map(db, client_id)
        self.location_cohorts = {
            category.id: _separate_location_cohorts(
                mapping_categories,
                self.catalog,
                target_category=category,
                product_gate=product_entities(
                    products.get(category.product_id) if category.product_id else None,
                    aliases,
                ),
                aliases=aliases,
            ).get(category.product_id, {})
            for category in candidates
        }
        self._employee_gates = [
            employee_entity(employee.attribute_values, aliases)
            for employee in self._employees
        ]
        self._gates = {
            category.id: category_entity_gate(
                category,
                product_entities(
                    products.get(category.product_id) if category.product_id else None, aliases
                ),
                aliases,
            )
            for category in categories.values()
        }
        self._matches: dict[str, list[int]] = {}
        self._cohorts: dict[str, _CohortIdentity] = {}
        self._specificities: dict[str, int] = {}
        self._best: dict[
            tuple[str | None, int], tuple[int, set[_CohortIdentity]]
        ] = {}
        for category in confirmed:
            self.accept(category)

    def _ensure_matches(self, category: Category) -> list[int]:
        if category.id not in self._matches:
            gate = self._gates[category.id]
            self._matches[category.id] = [
                index
                for index, (view, employee_gate) in enumerate(
                    zip(self._views, self._employee_gates, strict=True)
                )
                if category.matching_rule
                and _entity_allows(gate, employee_gate)
                and location_allows(category, view)
                and evaluate(category.matching_rule, view)
            ]
            self._cohorts[category.id] = (
                category_signature(category.raw_description),
                gate,
            )
            self._specificities[category.id] = category_specificity(category)
        return self._matches[category.id]

    def assessment(self, category: Category) -> tuple[int, int]:
        matches = self._ensure_matches(category)
        cohort = self._cohorts[category.id]
        specificity = self._specificities[category.id]
        matched = 0
        overlaps = 0
        for index in matches:
            best = self._best.get((category.product_id, index))
            if best is None or specificity > best[0]:
                matched += 1
            elif specificity == best[0]:
                matched += 1
                if best[1] - {cohort}:
                    overlaps += 1
            elif cohort in best[1]:
                matched += 1
        return matched, overlaps

    def accept(self, category: Category) -> None:
        matches = self._ensure_matches(category)
        cohort = self._cohorts[category.id]
        specificity = self._specificities[category.id]
        for index in matches:
            key = category.product_id, index
            best = self._best.get(key)
            if best is None or specificity > best[0]:
                self._best[key] = (specificity, {cohort})
            elif specificity == best[0]:
                best[1].add(cohort)


def confirm_category_mapping(
    db: Session,
    *,
    category: Category,
    client_id: str,
    batch: CategoryConfirmationBatch | None = None,
) -> EligibilityMappingProfile:
    """Validate and persist one broker-confirmed reusable mapping profile."""

    batch = batch or CategoryConfirmationBatch(
        db,
        policy_year_id=category.policy_year_id,
        client_id=client_id,
        candidates=[category],
    )
    catalog = batch.catalog
    validation = validate_ai_matching_rule(
        category.raw_description,
        category.matching_rule,
        catalog,
        location_exclusions=batch.location_cohorts.get(category.id),
    )
    if not validation.valid:
        raise ValueError("; ".join(validation.errors))
    matched_count, overlap_count = batch.assessment(category)
    if overlap_count:
        noun = "employee" if overlap_count == 1 else "employees"
        verb = "matches" if overlap_count == 1 else "match"
        raise ValueError(f"{overlap_count} {noun} {verb} equally specific employee categories")
    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    expected_raw = pa.get("num_employees")
    expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
    matched = matched_count if catalog.roster_present else None
    warnings, _ = _rule_messages(
        catalog=catalog,
        matching_rule=category.matching_rule,
        validation=validation,
        matched=matched,
        expected=expected,
        overlap_count=0,
        unresolved=[],
    )
    existing = category.rule_validation if isinstance(category.rule_validation, dict) else {}
    profile_rule = _rule_without_product_location_context(category.matching_rule, existing)
    profile_validation = validate_matching_rule(
        profile_rule,
        catalog,
        allowed_values=_source_allowed_values(category.raw_description, catalog),
    )
    proposal = RuleProposal(
        rule=profile_rule,
        human_readable=(
            category.display_name
            if profile_rule != category.matching_rule
            else category.rule_human_readable or category.display_name
        ),
        confidence=float(category.confidence or 0.85),
        source="manual" if category.human_modified else str(existing.get("source") or "confirmed"),
        validation_state="validated",
        referenced_attributes=profile_validation.referenced_attributes,
        relative_remainder=bool(existing.get("relative_remainder")),
    )
    payload = {
        **existing,
        "state": "validated",
        "errors": [],
        "warnings": warnings,
        "overlap_count": 0,
        "unresolved_clauses": [],
        "required_attributes": validation.referenced_attributes,
        "matched_count": matched,
        "expected_count": expected,
        "confirmed": True,
    }
    profile = _upsert_profile(
        db,
        client_id=client_id,
        policy_year_id=category.policy_year_id,
        category=category,
        proposal=proposal,
        status="confirmed",
        validation={
            key: value
            for key, value in payload.items()
            if key != "product_location_exclusions"
        },
    )
    category.mapping_profile_id = profile.id
    category.rule_status = "validated"
    category.rule_validation = payload
    category.status = CategoryStatus.confirmed.value
    batch.accept(category)
    return profile
