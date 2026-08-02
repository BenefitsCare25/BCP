"""Shared construction of manual eligibility ``Category`` rows.

Two flows create categories from a free-text description: the category cards'
``POST /categories`` (status ``needs_review``) and the first-confirm seed in
``_materialize_categories`` (status ``confirmed``). Both derive the matching
rule from the description and share the same provenance envelope, so the build
lives here once to keep them from drifting.
"""
from __future__ import annotations

from typing import Any

from app.models import Category
from app.models.category import SourceKind
from app.services.rule_generator import description_to_rule


def build_manual_category(
    *,
    policy_year_id: str,
    product_id: str | None,
    priority: int,
    display_name: str,
    source_ref: str,
    status: str,
    modified_by: str | None,
    participation_model: str | None = None,
    participation_detail: dict[str, Any] | None = None,
    plan_assignments: dict[str, Any] | None = None,
) -> Category:
    """Build (but do not persist) a manual Category whose matching rule is
    seeded from ``display_name``. Callers vary only status/source_ref and the
    participation / plan-assignment payloads."""
    name = display_name.strip()
    envelope = description_to_rule(name)
    return Category(
        policy_year_id=policy_year_id,
        product_id=product_id,
        priority=priority,
        display_name=name[:512],
        raw_description=name[:2048],
        matching_rule=envelope.rule,
        rule_human_readable=envelope.human_readable,
        participation_model=participation_model,
        participation_detail=participation_detail,
        plan_assignments=plan_assignments,
        source=SourceKind.manual.value,
        source_ref=source_ref,
        confidence=envelope.confidence,
        status=status,
        human_modified=True,
        modified_by=modified_by,
    )
