"""Clone a policy year's CONFIGURATION into a new (empty) benefit year.

Copies only configuration rows — categories (with their ``plan_assignments``:
voluntary rate tables, tier labels, benefit values), plans (Schedule of
Benefits), product terms (GST + free-cover limit), product-setup drafts, the
flex scheme, flex pricing matrix, and leave policy. Operational data (employees,
dependants, claims, enrollment windows/elections, plan overrides,
underwriting cases, placement-slip uploads) is intentionally NOT copied: a new
benefit year inherits the prior config but starts with a fresh roster/workflow.
Panel-clinic tags are already carried over when the year is created.

References that live INSIDE JSON payloads are remapped through an old→new
category-id map so they keep pointing at the cloned rows:

- ``flex_pricing`` price-tag keys are ``"<tier_category_id>::<plan_code>"``.

Absolute dates tied to the source year are dropped rather than carried:

- product-term coverage dates (left null → inherit the new year's span),
- the flex scheme's ``meta.effective_start`` / ``effective_end`` window,
- insurer-issued policy numbers (per-placement, re-issued each year).

The caller owns the surrounding transaction (audit + commit); this only flushes.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import new_uuid
from app.models.category import Category
from app.models.flex_pricing import FlexPricing
from app.models.flex_scheme import FlexScheme, FlexSchemeStatus
from app.models.leave_policy import LeavePolicy
from app.models.plan import Plan
from app.models.product_setup import ProductSetup, ProductSetupStatus
from app.models.product_term import ProductTerm


def _remap_price_tags(pricing: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    """Rewrite the category id in each ``<tier_category_id>::<plan_code>`` key."""
    products = pricing.get("products")
    if not isinstance(products, dict):
        return pricing
    for pdata in products.values():
        if not isinstance(pdata, dict):
            continue
        tags = pdata.get("price_tags")
        if not isinstance(tags, dict):
            continue
        remapped: dict[str, Any] = {}
        for key, val in tags.items():
            cat_id, sep, rest = str(key).partition("::")
            new_key = f"{id_map.get(cat_id, cat_id)}{sep}{rest}" if sep else key
            remapped[new_key] = val
        pdata["price_tags"] = remapped
    return pricing


def clone_policy_year_config(
    db: Session,
    *,
    source_id: str,
    target_id: str,
    client_id: str,
) -> dict[str, int]:
    """Copy the source year's configuration into the target year.

    Returns per-table counts of rows copied (for the response / audit).
    """
    counts: dict[str, int] = {}
    id_map: dict[str, str] = {}

    # 1. Categories — the central config record. plan_assignments (voluntary
    #    rates, tier labels, benefit values) rides along verbatim.
    cats = db.execute(select(Category).where(Category.policy_year_id == source_id)).scalars().all()
    for c in cats:
        new_id = new_uuid()
        id_map[c.id] = new_id
        db.add(
            Category(
                id=new_id,
                policy_year_id=target_id,
                product_id=c.product_id,
                priority=c.priority,
                display_name=c.display_name,
                raw_description=c.raw_description,
                matching_rule=copy.deepcopy(c.matching_rule),
                rule_human_readable=c.rule_human_readable,
                participation_model=c.participation_model,
                participation_detail=copy.deepcopy(c.participation_detail),
                plan_assignments=copy.deepcopy(c.plan_assignments),
                source=c.source,
                source_ref=c.source_ref,
                confidence=c.confidence,
                status="needs_review",
                human_modified=c.human_modified,
                modified_by=c.modified_by,
            )
        )
    counts["categories"] = len(cats)

    # 2. Plans — per-plan Schedule of Benefits.
    plans = db.execute(select(Plan).where(Plan.policy_year_id == source_id)).scalars().all()
    for p in plans:
        db.add(
            Plan(
                id=new_uuid(),
                product_id=p.product_id,
                policy_year_id=target_id,
                code=p.code,
                display_name=p.display_name,
                benefit_schedule=copy.deepcopy(p.benefit_schedule),
                cover_description=p.cover_description,
                annual_policy_limit=p.annual_policy_limit,
                report_label=p.report_label,
                source=p.source,
                source_ref=p.source_ref,
                confidence=p.confidence,
                status="needs_review",
                human_modified=p.human_modified,
                modified_by=p.modified_by,
            )
        )
    counts["plans"] = len(plans)

    # 3. Product terms — GST opinion + free-cover limit. Coverage dates and the
    #    insurer policy number are year-specific, so they are dropped.
    terms = (
        db.execute(select(ProductTerm).where(ProductTerm.policy_year_id == source_id))
        .scalars()
        .all()
    )
    for t in terms:
        db.add(
            ProductTerm(
                id=new_uuid(),
                policy_year_id=target_id,
                product_id=t.product_id,
                coverage_start=None,
                coverage_end=None,
                gst_included=t.gst_included,
                gst_rate=t.gst_rate,
                free_cover_limit=t.free_cover_limit,
                nel_age_limit=t.nel_age_limit,
                underwriting_required=t.underwriting_required,
                policy_number=None,
            )
        )
    counts["product_terms"] = len(terms)

    # 4. Product-setup drafts — the resumable guided-form answers.
    setups = (
        db.execute(select(ProductSetup).where(ProductSetup.policy_year_id == source_id))
        .scalars()
        .all()
    )
    for s in setups:
        db.add(
            ProductSetup(
                id=new_uuid(),
                policy_year_id=target_id,
                product_code=s.product_code,
                template_version=s.template_version,
                answers=copy.deepcopy(s.answers),
                status=ProductSetupStatus.draft,
                origin=s.origin,
                origin_ref=s.origin_ref,
                confirmed_at=None,
                confirmed_by=None,
                materialized_product_id=None,
            )
        )
    counts["product_setups"] = len(setups)

    # 5. Flex scheme — blank the absolute effective window (inherits new span).
    scheme = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == source_id)
    ).scalar_one_or_none()
    if scheme is not None:
        scheme_bag = copy.deepcopy(scheme.scheme) or {}
        meta = scheme_bag.get("meta")
        if isinstance(meta, dict):
            meta["effective_start"] = ""
            meta["effective_end"] = ""
        db.add(
            FlexScheme(
                id=new_uuid(),
                policy_year_id=target_id,
                status=FlexSchemeStatus.draft,
                scheme=scheme_bag,
                origin=scheme.origin,
                source_ref=scheme.source_ref,
                confidence=scheme.confidence,
                confirmed_at=None,
                confirmed_by=None,
            )
        )
        counts["flex_scheme"] = 1

    # 6. Flex pricing matrix — remap category ids inside the price-tag keys.
    pricing = db.execute(
        select(FlexPricing).where(FlexPricing.policy_year_id == source_id)
    ).scalar_one_or_none()
    if pricing is not None:
        db.add(
            FlexPricing(
                id=new_uuid(),
                policy_year_id=target_id,
                client_id=client_id,
                pricing=_remap_price_tags(copy.deepcopy(pricing.pricing) or {}, id_map),
            )
        )
        counts["flex_pricing"] = 1

    # Leave terms are annual configuration. Member elections are operational
    # records and remain behind in the source year.
    leave = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == source_id)
    ).scalar_one_or_none()
    if leave is not None:
        db.add(
            LeavePolicy(
                id=new_uuid(),
                policy_year_id=target_id,
                client_id=client_id,
                allow_buy=leave.allow_buy,
                allow_sell=leave.allow_sell,
                min_buy_days=leave.min_buy_days,
                max_buy_days=leave.max_buy_days,
                min_sell_days=leave.min_sell_days,
                max_sell_days=leave.max_sell_days,
                increment_days=leave.increment_days,
                leave_rates=copy.deepcopy(leave.leave_rates) or {},
                notes=leave.notes,
            )
        )
        counts["leave_policy"] = 1

    db.flush()
    return counts
