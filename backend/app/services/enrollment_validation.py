"""Validation helpers for enrollment elections.

Centralizes the rules that an election or leave choice must satisfy: the window
must be open and within its dates, the chosen plan must be a real tier for the
product, named dependants must belong to the employee, and leave days must sit
within the member's bounds + increment (the day caps are per grade/designation
tier — see ``leave_pricing_resolver``). Raised errors are HTTPException so
routers stay thin.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dependant, Employee, LeavePolicy
from app.models.enrollment import ElectionAction
from app.models.enrollment_window import EnrollmentWindow, WindowStatus
from app.models.leave_election import LeaveAction
from app.services.leave_pricing_resolver import leave_limits_for

if TYPE_CHECKING:
    from app.services.cohort_tiers import CohortTier, ProductTierSet


def window_in_period(window: EnrollmentWindow) -> bool:
    """True when the window is open AND the current time is within its dates —
    the same rule `assert_window_accepts_edits` enforces, as a predicate (used
    by the portal to decide whether to surface enrollment at all)."""
    if window.status != WindowStatus.open:
        return False
    now = datetime.now(UTC)
    opens = _aware(window.opens_at)
    closes = _aware(window.closes_at)
    if opens and now < opens:
        return False
    return not (closes and now > closes)


def assert_window_accepts_edits(window: EnrollmentWindow) -> None:
    """An election may only be edited while its window is open and in-period."""
    if window.status != WindowStatus.open:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Window is {window.status}; elections can only change while it is open.",
        )
    now = datetime.now(UTC)
    opens = _aware(window.opens_at)
    closes = _aware(window.closes_at)
    if opens and now < opens:
        raise HTTPException(status.HTTP_409_CONFLICT, "The enrollment window has not opened yet.")
    if closes and now > closes:
        raise HTTPException(status.HTTP_409_CONFLICT, "The enrollment window has closed.")


def assert_not_compulsory_decline(
    product_code: str, compulsory_product_ids: set[str], product_id: str
) -> None:
    """Reject a decline election for a compulsory product."""
    if product_id in compulsory_product_ids:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Product '{product_code}' is compulsory and cannot be declined.",
        )


def assert_product_in_scope(window: EnrollmentWindow, product_code: str) -> None:
    scope = window.product_scope
    if scope and product_code not in scope:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Product '{product_code}' is not in this window's scope.",
        )


def assert_plan_available(
    plan_code: str | None, available: set[str], product_code: str
) -> None:
    # A named plan must match a configured tier. An empty `available` set means the
    # product has no electable plans, so any named plan is rejected (not waved
    # through) — otherwise a bogus plan_code would be written to an override.
    if plan_code and plan_code not in available:
        detail = (
            f"Product '{product_code}' has no configured plans to elect."
            if not available
            else f"Plan '{plan_code}' is not available for product '{product_code}'."
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def resolve_electable_tier(
    tier_set: ProductTierSet,
    plan_code: str | None,
    tier_category_id: str | None,
) -> CohortTier:
    """Resolve a (plan_code, tier_category_id) election to a tier in the cohort.

    Scopes the choice to the member's own cohort tiers — rejecting a plan from a
    different cohort and the empty 'keep current' choice maps to the baseline.
    Tiers are identified by the (tier_category_id, plan_code) PAIR: a category and
    a plan can each be shared across tiers (GPA "Option N" share a plan_code; a
    single-category product's synthesized plan tiers share the baseline category
    id), so only the pair is unique. The UI sends both; either alone is accepted
    when it resolves to exactly one tier.
    """
    tiers = tier_set.tiers
    if tier_category_id and plan_code:
        match = next(
            (
                t
                for t in tiers
                if t.tier_category_id == tier_category_id and t.plan_code == plan_code
            ),
            None,
        )
        if match is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Tier is not electable for product '{tier_set.product_code}'.",
            )
        return match
    if tier_category_id:
        matches = [t for t in tiers if t.tier_category_id == tier_category_id]
        if not matches:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Tier is not electable for product '{tier_set.product_code}'.",
            )
        if len(matches) > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Tier maps to several plans for '{tier_set.product_code}'; "
                "specify a plan_code.",
            )
        return matches[0]
    if plan_code:
        matches = [t for t in tiers if t.plan_code == plan_code]
        if not matches:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Plan '{plan_code}' is not in this member's cohort for "
                f"product '{tier_set.product_code}'.",
            )
        if len(matches) > 1:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Plan '{plan_code}' maps to several tiers for "
                f"'{tier_set.product_code}'; specify a tier_category_id.",
            )
        return matches[0]
    # No plan/tier named → keep the baseline tier.
    baseline = next((t for t in tiers if t.is_baseline), None)
    if baseline is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"No baseline tier for product '{tier_set.product_code}'.",
        )
    return baseline


def action_for_tier(
    previous_plan_code: str | None,
    tier: CohortTier,
    plan_rank: dict[str, int],
) -> str:
    """Election action from a resolved cohort tier.

    Driven by the tier's resolved ``direction`` so the stored action stays in
    sync with the (tag-bearing) direction the enrollment options expose:

    - ``upgrade`` / ``downgrade`` — record as-is.
    - ``same`` — provably identical coverage (equal known sum insured, e.g. an
      equal-SI voluntary duplicate of the compulsory tier) → ``keep``, NOT a
      plan-rank-guessed up/down.
    - ``unknown`` — direction couldn't be resolved (no SI and plan codes don't
      order). Fall back to the plan-rank heuristic so a real tier change (e.g.
      SILVER→GOLD) still registers as up/down rather than collapsing to ``keep``.

    The label is informational; the override projection depends on the plan code.
    """
    if not previous_plan_code and tier.plan_code:
        return ElectionAction.enroll
    if tier.is_baseline or tier.plan_code == previous_plan_code:
        return ElectionAction.keep
    if tier.direction == "upgrade":
        return ElectionAction.upgrade
    if tier.direction == "downgrade":
        return ElectionAction.downgrade
    if tier.direction == "same":
        return ElectionAction.keep
    return classify_action(previous_plan_code, tier.plan_code, False, plan_rank)


def assert_dependants_owned(
    db: Session, employee: Employee, dependant_ids: list[str]
) -> None:
    if not dependant_ids:
        return
    owned = set(
        db.execute(
            select(Dependant.id).where(
                Dependant.employee_id == employee.id,
                Dependant.id.in_(dependant_ids),
            )
        ).scalars()
    )
    missing = [d for d in dependant_ids if d not in owned]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Dependants do not belong to this employee: {', '.join(missing)}.",
        )


def assert_valid_dependant_options(
    dependant_option_ids: dict[str, str] | None,
    choices: dict[str, list[dict]],
) -> None:
    """Each elected dependant option level must be one of the product's ACTUAL
    electable choices for the elected tier (``dependant_option_choices`` — the
    same source the options API exposes and pricing resolves). Role keys are
    validated by the schema; this rejects any id pricing could never resolve:
    an employee category, a linked (marker/composition) option row, another
    product's row, or any id when the product offers no freestanding levels."""
    if not dependant_option_ids:
        return
    for role, cat_id in dependant_option_ids.items():
        valid = {c.get("category_id") for c in choices.get(role, [])}
        if cat_id not in valid:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"'{cat_id}' is not an electable {role} dependant option level "
                "of this product.",
            )


def validate_leave(
    policy: LeavePolicy | None,
    action: str,
    days: float,
    employee: Employee | None = None,
) -> None:
    """Validate a buy/sell-leave choice against the member's bounds + increment.

    The MAXIMA are per-tier (``leave_pricing_resolver.leave_limits_for`` — the
    member's grade/designation entry, else the policy default); the minimums and
    the increment are company-wide. Passing no ``employee`` validates against the
    defaults, which is the correct fallback for a caller with no member in hand.
    """
    if action == LeaveAction.none:
        if days not in (0, 0.0):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "days must be 0 when action is 'none'.",
            )
        return
    if policy is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No leave policy is configured for this year."
        )
    limits = leave_limits_for(policy, employee)
    if action == LeaveAction.buy:
        if not policy.allow_buy:
            raise HTTPException(status.HTTP_409_CONFLICT, "Buying leave is not permitted.")
        lo, hi = policy.min_buy_days, limits.max_buy_days
    elif action == LeaveAction.sell:
        if not policy.allow_sell:
            raise HTTPException(status.HTTP_409_CONFLICT, "Selling leave is not permitted.")
        lo, hi = policy.min_sell_days, limits.max_sell_days
    else:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown leave action '{action}'."
        )

    if days <= 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "days must be positive for buy/sell."
        )
    if days < lo or days > hi:
        # Name the tier when its own cap is what bit — otherwise the broker reads a
        # number that matches nothing on the policy's global fields.
        scope = (
            f" for '{limits.tier_value}'"
            if limits.from_tier and limits.tier_value
            else ""
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"days must be between {lo} and {hi} for '{action}'{scope}.",
        )
    inc = policy.increment_days or 1.0
    # Guard against float drift when checking divisibility by the increment.
    if inc > 0 and abs((days / inc) - round(days / inc)) > 1e-6:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"days must be in increments of {inc}.",
        )


def classify_action(
    previous_plan_code: str | None,
    elected_plan_code: str | None,
    declined: bool,
    plan_rank: dict[str, int],
) -> str:
    """Label an election relative to the baseline.

    Upgrade/downgrade direction is a heuristic from ``plan_rank`` (a higher rank
    = richer tier); ties or unknown codes collapse to a neutral label. The label
    is informational only — the projected override depends on the plan code, not
    on this.
    """
    if declined:
        return ElectionAction.decline
    if not previous_plan_code and elected_plan_code:
        return ElectionAction.enroll
    if elected_plan_code == previous_plan_code:
        return ElectionAction.keep
    prev_rank = plan_rank.get(previous_plan_code or "", -1)
    new_rank = plan_rank.get(elected_plan_code or "", -1)
    if new_rank > prev_rank:
        return ElectionAction.upgrade
    if new_rank < prev_rank:
        return ElectionAction.downgrade
    return ElectionAction.keep


def _aware(dt: datetime | None) -> datetime | None:
    """Coerce to an aware UTC datetime (SQLite returns naive)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
