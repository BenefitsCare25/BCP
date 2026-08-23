"""Flex price-tag matrix — per-policy-year config for flex-funded enrollment.

The "price tag" is the amount deducted from a member's Flexible-Benefits wallet
to offset elected coverage (distinct from the insurer premium). It varies by
electable tier and per-product age band. One matrix per policy year, upsert-by-year
(like the leave policy / flex scheme).

- GET /policy-years/{id}/flex-pricing  — saved matrix + the available product tiers
  (so the UI can render the grid: tiers as rows, age bands as columns)
- PUT /policy-years/{id}/flex-pricing   — upsert the matrix

Tenant scoping rides on ``load_policy_year``.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_editable, load_policy_year
from app.db.session import get_db
from app.models import Category, EnrollmentWindow, FlexPricing, PolicyYear, Product
from app.models.enrollment_window import WindowStatus
from app.services.cohort_tiers import list_product_tiers, tier_key
from app.services.enrollment_validation import assert_enrollment_config_editable
from app.services.flex_pricing_resolver import (
    DependantMode,
    _per_member_slip_premium,
    dependant_age_limits,
    family_slip_index_from,
    stamp_pricing,
    validate_pricing_shape,
)

router = APIRouter(tags=["flex-pricing"])


class FlexPricingTierOut(BaseModel):
    key: str
    label: str
    plan_code: str | None
    direction: str
    is_baseline: bool
    # Per-member annual premium from the placement slip for this tier (None when
    # the slip carries no per-member figure, e.g. a tiered hospital plan). Lets
    # the window form preview the "from slip" price tag without a second call.
    slip_premium: float | None = None
    # Per-member sum insured (basis) — drives the life-product live preview
    # (premium = sum_insured / 1000 x voluntary_rate[member age band]).
    sum_insured: float | None = None
    # The tier's own cohort (job-category) name, for disambiguating rows when a
    # plan repeats across cohorts and the UI can't fold them (they price
    # differently). None when unavailable. The "(Job Category: …)" grade-code
    # parenthetical is trimmed so the label stays short.
    cohort_label: str | None = None


class FlexPricingProductOut(BaseModel):
    product_id: str
    product_code: str
    # Insurance line — "life" | "medical" | "flex" — for the UI's product label
    # and for choosing the age-banded (life) vs tiered (medical) layout.
    line: str = "medical"
    # Default config shape derived from the product's insurance line:
    # "age_banded" (life products, premiums rise with age) or "plan_type" (one
    # price per plan). A per-policy-year override lives in the pricing bag
    # (``products[id].mode``); the UI prefers that and falls back to this default.
    pricing_mode: str
    # Age-banded voluntary rate table (life products) — the rate-by-age table the
    # UI shows + uses for the live per-member premium preview. None for medical.
    voluntary_rates: list[dict[str, Any]] | None = None
    # Effective dependant eligibility windows (configured over defaults): a
    # dependant outside their role's window is not covered.
    dependant_age_limits: dict[str, dict[str, int]] = {}
    tiers: list[FlexPricingTierOut]
    # Suggested dependant pricing mode from the slip: "per_pax" when the slip lists a
    # per-dependant rate (e.g. GCGP), "family_group" when it carries an EO/ES/EC/EF
    # rate table, else "none". The saved config lives in the pricing bag
    # (``products[id].dependant``); the UI prefers that.
    dependant_suggested_mode: str = DependantMode.none
    # Slip-derived family increments, keyed by tier_key → role → amount over
    # Employee-Only (family_group prefill; the amount differs per plan).
    slip_family: dict[str, dict[str, float]] = {}
    # Slip-derived per-dependant rate, keyed by tier_key → rate (per_pax prefill).
    slip_per_pax: dict[str, float] = {}


class FlexPricingOut(BaseModel):
    policy_year_id: str
    pricing: dict[str, Any]
    products: list[FlexPricingProductOut]


class FlexPricingIn(BaseModel):
    pricing: dict[str, Any]


class EnrollmentPricingConfigIn(FlexPricingIn):
    flex_price_source: dict[str, Literal["slip", "manual"]]


def _cohort_label(display_name: str | None) -> str | None:
    """A short cohort name from a category display name — the human part with the
    "(Job Category: …)" grade-code parenthetical trimmed off (kept when the whole
    name is that parenthetical)."""
    if not display_name:
        return None
    trimmed = re.split(r"\s*\(job category", display_name, maxsplit=1, flags=re.IGNORECASE)[0]
    return trimmed.strip() or display_name.strip()


def _available_products(
    db: Session, policy_year_id: str, pricing: dict[str, Any] | None = None
) -> list[FlexPricingProductOut]:
    tier_sets = list_product_tiers(db, policy_year_id)
    pids = [ts.product_id for ts in tier_sets.values()]
    # Per-tier cohort label (its own category's name), for the divergence-split /
    # age-banded rows the UI can't fold — loaded once for every tier category.
    cat_ids = {t.tier_category_id for ts in tier_sets.values() for t in ts.tiers}
    label_by_cat: dict[str, str | None] = {}
    if cat_ids:
        for cid, name in db.execute(
            select(Category.id, Category.display_name).where(Category.id.in_(cat_ids))
        ).all():
            label_by_cat[cid] = _cohort_label(name)
    mode_by_id: dict[str, str] = {}
    line_by_id: dict[str, str] = {}
    if pids:
        for p in db.execute(select(Product).where(Product.id.in_(pids))).scalars():
            mode_by_id[p.id] = p.flex_pricing_mode
            line_by_id[p.id] = p.line
    # Slip-derived dependant pricing per tier — either family increments
    # (role → amount over Employee-Only) or a {"per_pax": rate} per-dependant rate.
    # Built from the tier sets already loaded above (no second list_product_tiers).
    # Split into the two prefill shapes the config grid renders, and suggest the
    # matching mode (per_pax when the slip lists a Dependents rate, else family_group).
    fam_idx = family_slip_index_from(tier_sets)
    out: list[FlexPricingProductOut] = []
    for ts in sorted(tier_sets.values(), key=lambda s: s.product_code):
        rows = fam_idx.get(ts.product_id) or {}
        slip_per_pax = {
            k: v["per_pax"] for k, v in rows.items()
            if isinstance(v, dict) and "per_pax" in v
        }
        slip_family = {
            k: v for k, v in rows.items()
            if isinstance(v, dict) and "per_pax" not in v
        }
        if slip_per_pax:
            suggested = DependantMode.per_pax
        elif slip_family:
            suggested = DependantMode.family_group
        else:
            suggested = DependantMode.none
        # The voluntary rate table is a product-level property; take it from the
        # first tier that carries one (voluntary life tiers all share it).
        voluntary_rates = next(
            (
                [b.model_dump() for b in t.financials.voluntary_rates]
                for t in ts.tiers
                if t.financials is not None and t.financials.voluntary_rates
            ),
            None,
        )
        out.append(
            FlexPricingProductOut(
                product_id=ts.product_id,
                product_code=ts.product_code,
                line=line_by_id.get(ts.product_id, "medical"),
                pricing_mode=mode_by_id.get(ts.product_id, "plan_type"),
                voluntary_rates=voluntary_rates,
                dependant_age_limits=dependant_age_limits(pricing, ts.product_id),
                dependant_suggested_mode=suggested,
                slip_family=slip_family,
                slip_per_pax=slip_per_pax,
                tiers=[
                    FlexPricingTierOut(
                        key=tier_key(t.tier_category_id, t.plan_code),
                        label=t.label,
                        plan_code=t.plan_code,
                        direction=t.direction,
                        is_baseline=t.is_baseline,
                        slip_premium=_per_member_slip_premium(t.financials),
                        sum_insured=t.financials.sum_insured if t.financials else None,
                        cohort_label=label_by_cat.get(t.tier_category_id),
                    )
                    # Hide tiers that can't be priced — no slip financials AND no
                    # plan code (e.g. unconfigured job-category eligibility rows
                    # matched to a product that prices by plan). They'd render as
                    # blank "—" plans the broker can't act on.
                    for t in ts.tiers
                    if t.financials is not None or t.plan_code
                ],
            )
        )
    return out


@router.get(
    "/policy-years/{policy_year_id}/flex-pricing", response_model=FlexPricingOut
)
def get_flex_pricing(
    policy_year_id: str,
    py: PolicyYear = Depends(load_policy_year),
    db: Session = Depends(get_db),
) -> FlexPricingOut:
    row = db.execute(
        select(FlexPricing).where(FlexPricing.policy_year_id == py.id)
    ).scalar_one_or_none()
    pricing = (row.pricing if row else {}) or {}
    # The returned bag stays raw (the client edits + re-saves it); the effective
    # dependant age-limit display resolves against a stamped copy of the SAME
    # already-loaded row, so it reflects the scheme-level default
    # (meta.dependant_age_limits) too without re-querying the pricing row.
    return FlexPricingOut(
        policy_year_id=py.id,
        pricing=pricing,
        products=_available_products(db, py.id, stamp_pricing(db, py.id, pricing)),
    )


def _upsert_pricing_row(
    db: Session,
    py: PolicyYear,
    pricing: dict[str, Any],
) -> tuple[FlexPricing, str]:
    errors = validate_pricing_shape(pricing)
    if errors:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Malformed flex pricing.", "errors": errors},
        )
    row = db.execute(
        select(FlexPricing).where(FlexPricing.policy_year_id == py.id)
    ).scalar_one_or_none()
    action = "update_flex_pricing"
    if row is None:
        row = FlexPricing(policy_year_id=py.id, client_id=py.client_id)
        db.add(row)
        action = "set_flex_pricing"
    row.pricing = pricing
    flag_modified(row, "pricing")
    db.flush()
    return row, action


@router.put(
    "/policy-years/{policy_year_id}/flex-pricing", response_model=FlexPricingOut
)
def upsert_flex_pricing(
    policy_year_id: str,
    body: FlexPricingIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FlexPricingOut:
    assert_policy_year_editable(py)
    assert_enrollment_config_editable(db, py.id, "Flex pricing")
    row, action = _upsert_pricing_row(db, py, body.pricing)
    write_audit(
        db, user, action=action, entity_type="flex_pricing",
        entity_id=row.id, after={"products": list(body.pricing.get("products", {}))},
    )
    db.commit()
    db.refresh(row)
    pricing = row.pricing or {}
    return FlexPricingOut(
        policy_year_id=py.id,
        pricing=pricing,
        products=_available_products(db, py.id, pricing),
    )


@router.put(
    "/policy-years/{policy_year_id}/enrollment-pricing-config",
    response_model=FlexPricingOut,
)
def upsert_enrollment_pricing_config(
    policy_year_id: str,
    body: EnrollmentPricingConfigIn,
    py: PolicyYear = Depends(load_policy_year),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FlexPricingOut:
    """Save the matrix and every draft period's source as one transaction."""
    assert_policy_year_editable(py)
    assert_enrollment_config_editable(db, py.id, "Flex pricing")
    windows = db.execute(
        select(EnrollmentWindow).where(
            EnrollmentWindow.policy_year_id == py.id,
            EnrollmentWindow.status == WindowStatus.draft,
        )
    ).scalars().all()
    if not windows:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Create a draft enrolment period before configuring price tags.",
        )
    row, _action = _upsert_pricing_row(db, py, body.pricing)
    for window in windows:
        window.flex_price_source = dict(body.flex_price_source)
    write_audit(
        db,
        user,
        action="update_enrollment_pricing_config",
        entity_type="flex_pricing",
        entity_id=row.id,
        after={
            "products": list(body.pricing.get("products", {})),
            "window_ids": [window.id for window in windows],
        },
    )
    db.commit()
    db.refresh(row)
    pricing = row.pricing or {}
    return FlexPricingOut(
        policy_year_id=py.id,
        pricing=pricing,
        products=_available_products(db, py.id, pricing),
    )
