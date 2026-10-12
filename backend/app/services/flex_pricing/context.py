"""Window / year flex configuration: the price-source map and drawdown rule,
the slip index builders, the age reference date, and the per-year context.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import Employee, PolicyYear
from app.models.enrollment_window import EnrollmentWindow, FlexDrawdownRule, WindowStatus
from app.services.flex_pricing.bag import _uses_slip, get_pricing
from app.services.flex_pricing.price_tags import slip_premium_index, slip_premium_index_from
from app.services.flex_pricing.slip_family import (
    _merge_family_overlay,
    dependant_option_overlay,
    family_slip_index,
    family_slip_index_from,
)
from app.services.roster_attributes import age_from_attrs


def maybe_family_slip_index(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build the family slip index when any product resolves to the slip source
    (``_uses_slip``) — an unconfigured slip product defaults to family_group priced
    from the slip, so the index is needed to both decide the mode and price it. An
    explicit all-manual window skips it."""
    return family_slip_index(db, policy_year_id) if _uses_slip(source_map) else None


def window_flex_config(window: EnrollmentWindow) -> tuple[dict[str, str], str]:
    """``(source_map, drawdown_rule)`` for one window. A product absent from the map
    defaults to the slip source (``DEFAULT_FLEX_SOURCE``); the drawdown rule
    defaults to full."""
    src = window.flex_price_source if isinstance(window.flex_price_source, dict) else {}
    return src, (window.flex_drawdown_rule or FlexDrawdownRule.full)


def governing_flex_config(
    db: Session, policy_year_id: str
) -> tuple[dict[str, str], str]:
    """The flex config for window-agnostic surfaces (the benefit statement).

    The drawdown rule is a company-wide setting, so the most recent non-draft
    window for the year governs; with no window yet, returns ({}, full) — an empty
    source map means every product defaults to the slip source."""
    w = (
        db.execute(
            select(EnrollmentWindow)
            .where(
                EnrollmentWindow.policy_year_id == policy_year_id,
                EnrollmentWindow.status != WindowStatus.draft,
            )
            .order_by(EnrollmentWindow.opens_at.desc())
        )
        .scalars()
        .first()
    )
    return window_flex_config(w) if w is not None else ({}, FlexDrawdownRule.full)


def maybe_slip_index(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Build the slip-premium index when any product resolves to the slip source
    (``_uses_slip`` — the default). Only an explicit all-manual window skips it."""
    return slip_premium_index(db, policy_year_id) if _uses_slip(source_map) else None


def maybe_slip_indices(
    db: Session, policy_year_id: str, source_map: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """``(slip_premium_idx, family_slip_idx)`` from a SINGLE ``list_product_tiers``
    load when any product uses the slip source, else ``(None, None)``. Use this on
    paths that need both (the benefit statement) so the expensive tier load runs
    once instead of once per index."""
    if not _uses_slip(source_map):
        return None, None
    from app.services.cohort_tiers import list_product_tiers

    tier_sets = list_product_tiers(db, policy_year_id)
    return (
        slip_premium_index_from(tier_sets),
        _merge_family_overlay(
            family_slip_index_from(tier_sets),
            dependant_option_overlay(db, policy_year_id),
        ),
    )


def employee_age(employee: Employee, ref: date) -> int | None:
    """Member's actual age (last birthday) as of ``ref``, from their DOB."""
    return age_from_attrs(employee.attribute_values, ref)


def reference_date(db: Session, policy_year_id: str) -> date:
    """The date used to compute ages — the policy year's start (today as fallback)."""
    py = db.get(PolicyYear, policy_year_id)
    return py.start_date if py and py.start_date else business_today()


@dataclass(frozen=True)
class FlexYearContext:
    """The YEAR-level half of a flex resolution, built once.

    Everything here is keyed on the policy year alone, so recomputing it per
    member is pure waste — and `maybe_slip_indices` is not cheap waste: it runs
    a full `list_product_tiers` load. On CDL's 491-member roster the wallet
    report spent 31 of its 35 seconds rebuilding this same object 491 times.

    Passed in via `summarize_employee(..., context=...)`. Absent, that function
    builds its own and behaves exactly as before, so every existing per-member
    caller is unaffected — this is an opt-in for the surfaces that sweep a whole
    roster (the wallet ledger, its summary, the leaver sheet).
    """

    pricing: dict[str, Any] | None
    reference_date: date
    source_map: dict[str, str]
    rule: str
    slip_idx: dict[str, Any] | None
    family_slip_idx: dict[str, Any] | None


def flex_year_context(db: Session, policy_year_id: str) -> FlexYearContext:
    source_map, rule = governing_flex_config(db, policy_year_id)
    slip_idx, family_slip_idx = maybe_slip_indices(db, policy_year_id, source_map)
    return FlexYearContext(
        pricing=get_pricing(db, policy_year_id),
        reference_date=reference_date(db, policy_year_id),
        source_map=source_map,
        rule=rule,
        slip_idx=slip_idx,
        family_slip_idx=family_slip_idx,
    )
