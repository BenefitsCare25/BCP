"""Reusable mapping profiles and prior-year rule reuse."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, EligibilityMappingProfile, PolicyYear
from app.models.category import CategoryStatus
from app.services.eligibility_mapping.base import AttributeValueCatalog, RuleProposal
from app.services.eligibility_mapping.proposal import propose_category_rule
from app.services.eligibility_mapping.validation import (
    _source_allowed_values,
    validate_matching_rule,
)
from app.services.eligibility_mapping.vocabulary import category_signature


def _profile_proposal(profile: EligibilityMappingProfile) -> RuleProposal:
    validation = profile.validation if isinstance(profile.validation, dict) else {}
    unresolved = validation.get("unresolved_clauses")
    return RuleProposal(
        rule=profile.matching_rule,
        human_readable=profile.rule_human_readable or profile.display_name,
        confidence=float(profile.confidence or 0.85),
        source="prior_mapping",
        validation_state="proposed",
        unresolved_clauses=(
            [str(value) for value in unresolved] if isinstance(unresolved, list) else []
        ),
        referenced_attributes=list(profile.required_attributes or []),
        relative_remainder=bool(validation.get("relative_remainder")),
    )


def _previous_confirmed_rules(
    db: Session, policy_year_id: str, client_id: str
) -> dict[str, Category]:
    rows = list(
        db.execute(
            select(Category)
            .join(PolicyYear, PolicyYear.id == Category.policy_year_id)
            .where(
                PolicyYear.client_id == client_id,
                Category.policy_year_id != policy_year_id,
                Category.status == CategoryStatus.confirmed.value,
                Category.matching_rule.is_not(None),
            )
            .order_by(PolicyYear.year.desc(), Category.updated_at.desc())
        ).scalars()
    )
    out: dict[str, Category] = {}
    for category in rows:
        out.setdefault(category_signature(category.raw_description), category)
    return out


def _upsert_profile(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str,
    category: Category,
    proposal: RuleProposal,
    status: str,
    validation: dict[str, Any],
) -> EligibilityMappingProfile:
    signature = category_signature(category.raw_description)
    profile = db.execute(
        select(EligibilityMappingProfile).where(
            EligibilityMappingProfile.client_id == client_id,
            EligibilityMappingProfile.category_signature == signature,
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = EligibilityMappingProfile(
            client_id=client_id,
            category_signature=signature,
            display_name=category.display_name,
        )
        db.add(profile)
        db.flush()
    # A fresh machine proposal cannot overwrite a broker-confirmed reusable
    # profile. Confirm flow passes ``status=confirmed`` and may update it.
    if profile.status != "confirmed" or status == "confirmed":
        profile.display_name = category.display_name
        profile.matching_rule = proposal.rule
        profile.rule_human_readable = proposal.human_readable
        profile.required_attributes = proposal.referenced_attributes
        profile.validation = validation
        profile.source = proposal.source
        profile.confidence = proposal.confidence
        profile.status = status
        profile.last_policy_year_id = policy_year_id
    return profile


def _candidate_for_category(
    category: Category,
    catalog: AttributeValueCatalog,
    profiles: dict[str, EligibilityMappingProfile],
    previous: dict[str, Category],
) -> tuple[RuleProposal, bool]:
    signature = category_signature(category.raw_description)
    if profile := profiles.get(signature):
        if profile.status == "confirmed" and profile.matching_rule:
            return _profile_proposal(profile), True
    if prior := previous.get(signature):
        return (
            RuleProposal(
                rule=prior.matching_rule,
                human_readable=prior.rule_human_readable or prior.display_name,
                confidence=float(prior.confidence or 0.85),
                source="prior_year",
                validation_state="proposed",
                referenced_attributes=validate_matching_rule(
                    prior.matching_rule,
                    catalog,
                    allowed_values=_source_allowed_values(category.raw_description, catalog),
                ).referenced_attributes,
            ),
            True,
        )
    return propose_category_rule(category.raw_description, catalog), False
