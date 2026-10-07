"""Policy-year auto-mapping: propose, validate, and persist category rules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, EligibilityMappingProfile, Product
from app.models.category import CategoryStatus
from app.services.eligibility_mapping.assessment import _rule_messages, _rule_status
from app.services.eligibility_mapping.assignment import _assignment_counts
from app.services.eligibility_mapping.base import (
    LISTING_RULE_SOURCE,
    MappingItem,
    MappingSummary,
    RuleProposal,
    RuleValidation,
)
from app.services.eligibility_mapping.catalog import build_attribute_catalog
from app.services.eligibility_mapping.location_cohorts import (
    _exclude_separate_location_cohorts,
    _scope_catch_all,
    _separate_location_cohorts,
)
from app.services.eligibility_mapping.predicates import (
    is_bulk_confirmable,
    is_employee_mapping_category,
)
from app.services.eligibility_mapping.profiles import (
    _candidate_for_category,
    _previous_confirmed_rules,
    _upsert_profile,
)
from app.services.eligibility_mapping.summary import missing_category_plans
from app.services.eligibility_mapping.validation import validate_ai_matching_rule
from app.services.eligibility_scope import (
    location_cohort_value,
    location_scope,
    multi_location_scoped,
)
from app.services.matching_engine import entity_alias_map, product_entities


def auto_map_policy_year(
    db: Session,
    *,
    policy_year_id: str,
    client_id: str,
    persist_profiles: bool = True,
) -> MappingSummary:
    """Generate and persist company-aware proposals for one policy year.

    Existing human-authored non-null rules are validated but never overwritten.
    A confirmed category with ``rule=None`` is always returned to review.
    The caller owns transaction commit and audit logging.
    """

    catalog, employees, views = build_attribute_catalog(db, policy_year_id, client_id)
    all_categories = list(
        db.execute(
            select(Category)
            .where(Category.policy_year_id == policy_year_id)
            .order_by(Category.product_id, Category.priority)
        ).scalars()
    )
    categories = [
        category for category in all_categories if is_employee_mapping_category(category)
    ]
    not_applicable = len(all_categories) - len(categories)
    profiles = {
        profile.category_signature: profile
        for profile in db.execute(
            select(EligibilityMappingProfile).where(
                EligibilityMappingProfile.client_id == client_id
            )
        ).scalars()
    }
    previous = _previous_confirmed_rules(db, policy_year_id, client_id)
    products = {
        product.id: product
        for product in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories if c.product_id})
            )
        ).scalars()
    }
    aliases = entity_alias_map(db, client_id)
    location_exclusions = {
        category.id: _separate_location_cohorts(
            categories,
            catalog,
            target_category=category,
            product_gate=product_entities(
                products.get(category.product_id) if category.product_id else None,
                aliases,
            ),
            aliases=aliases,
        ).get(category.product_id, {})
        for category in categories
    }
    location_scoped = multi_location_scoped(categories)
    proposal_meta: dict[str, tuple[RuleProposal, bool, RuleValidation]] = {}
    profile_proposals: dict[str, RuleProposal] = {}

    for category in categories:
        # A broker-edited OR broker-confirmed rule is never recompiled: this runs
        # on every "Re-run matching", and re-proposing a confirmed rule could
        # demote the category (and un-publish its plan) without anyone asking.
        # Rules learned from the company's Employee Listing are kept too: they
        # reflect how the company actually assigns people, not slip wording.
        from_listing = isinstance(category.rule_validation, dict) and (
            category.rule_validation.get("source") == LISTING_RULE_SOURCE
        )
        preserve = bool(
            category.matching_rule
            and (
                category.human_modified
                or category.status == CategoryStatus.confirmed.value
                or from_listing
            )
        )
        if preserve:
            validation = validate_ai_matching_rule(
                category.raw_description,
                category.matching_rule,
                catalog,
                location_exclusions=location_exclusions[category.id],
            )
            proposal = RuleProposal(
                rule=category.matching_rule,
                human_readable=category.rule_human_readable or category.display_name,
                confidence=float(category.confidence or 0.85),
                source=LISTING_RULE_SOURCE if from_listing and not category.human_modified
                else "manual",
                validation_state="proposed",
                referenced_attributes=validation.referenced_attributes,
            )
            reused = False
        else:
            proposal, reused = _candidate_for_category(category, catalog, profiles, previous)
            if category.id in location_scoped:
                proposal = _scope_catch_all(proposal, category, catalog)
            profile_proposals[category.id] = proposal
            proposal = _exclude_separate_location_cohorts(
                proposal, category, location_exclusions[category.id]
            )
            validation = validate_ai_matching_rule(
                category.raw_description,
                proposal.rule,
                catalog,
                location_exclusions=location_exclusions[category.id],
            )
            category.matching_rule = proposal.rule
            category.rule_human_readable = proposal.human_readable
            category.confidence = proposal.confidence

        category.rule_status = (
            "unmapped"
            if proposal.rule is None
            else "needs_review"
            if proposal.unresolved_clauses or not validation.valid
            else "proposed"
        )
        if category.matching_rule is None and category.status == CategoryStatus.confirmed.value:
            category.status = CategoryStatus.needs_review.value
        proposal_meta[category.id] = (proposal, reused, validation)

    counts, overlaps = _assignment_counts(
        db=db,
        client_id=client_id,
        categories=categories,
        employees=employees,
        views=views,
        excluded_category_ids={
            category_id
            for category_id, (_, _, validation) in proposal_meta.items()
            if not validation.valid
        },
        validated_category_ids={
            category_id
            for category_id, (_, _, validation) in proposal_meta.items()
            if validation.valid
        },
    )

    items: list[MappingItem] = []
    for category in categories:
        proposal, reused, validation = proposal_meta[category.id]
        pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
        expected_raw = pa.get("num_employees")
        expected = int(expected_raw) if isinstance(expected_raw, (int, float)) else None
        matched = counts.get(category.id, 0) if catalog.roster_present else None
        errors = list(validation.errors)
        warnings, blockers = _rule_messages(
            catalog=catalog,
            matching_rule=proposal.rule,
            validation=validation,
            matched=matched,
            expected=expected,
            overlap_count=overlaps.get(category.id, 0),
            unresolved=proposal.unresolved_clauses,
        )
        rule_status = _rule_status(
            matching_rule=proposal.rule,
            validation=validation,
            blockers=blockers,
            roster_present=catalog.roster_present,
        )
        category.rule_status = rule_status
        payload = {
            "state": rule_status,
            "source": proposal.source,
            "errors": errors,
            "warnings": warnings,
            "overlap_count": overlaps.get(category.id, 0),
            "unresolved_clauses": proposal.unresolved_clauses,
            "required_attributes": validation.referenced_attributes,
            "matched_count": matched,
            "expected_count": expected,
            "relative_remainder": proposal.relative_remainder,
            "reused": reused,
        }
        if scope := location_scope(category):
            scope_attr, scope_value = location_cohort_value(scope, catalog.values)
            if scope_attr and scope_value is not None:
                payload["location_scope_rule"] = {"=": [scope_attr, scope_value]}
        if (
            category.id in profile_proposals
            and proposal.rule != profile_proposals[category.id].rule
        ):
            payload["product_location_exclusions"] = location_exclusions[category.id]
        category.rule_validation = payload
        # A location-narrowed rule is specific to this product's office split;
        # stored under the shared "All Employees" signature it would overwrite
        # (and later be reused for) every other product's catch-all.
        if (
            persist_profiles
            and proposal.rule is not None
            and category.id not in location_scoped
        ):
            profile = _upsert_profile(
                db,
                client_id=client_id,
                policy_year_id=policy_year_id,
                category=category,
                proposal=profile_proposals.get(category.id, proposal),
                status="proposed",
                validation=payload,
            )
            category.mapping_profile_id = profile.id

        product = products.get(category.product_id) if category.product_id else None
        items.append(
            MappingItem(
                category_id=category.id,
                product_code=product.code if product else None,
                display_name=category.display_name,
                plan_code=str(pa.get("plan_code") or "") or None,
                category_status=category.status,
                rule_status=rule_status,
                source=proposal.source,
                matching_rule=category.matching_rule,
                rule_human_readable=category.rule_human_readable,
                confidence=category.confidence,
                matched_count=matched,
                expected_count=expected,
                unresolved_clauses=list(proposal.unresolved_clauses),
                errors=errors,
                warnings=warnings,
                reused=reused,
                bulk_confirmable=is_bulk_confirmable(category),
            )
        )

    missing = missing_category_plans(db, policy_year_id=policy_year_id)
    return MappingSummary(
        policy_year_id=policy_year_id,
        employee_count=catalog.employee_count,
        total=len(items),
        validated=sum(item.rule_status == "validated" for item in items),
        proposed=sum(item.rule_status == "proposed" for item in items),
        needs_review=sum(item.rule_status == "needs_review" for item in items),
        unmapped=sum(item.rule_status == "unmapped" for item in items),
        not_applicable=not_applicable,
        reused=sum(item.reused for item in items),
        categories=items,
        missing_categories=len(missing),
        missing_category_plans=missing,
    )
