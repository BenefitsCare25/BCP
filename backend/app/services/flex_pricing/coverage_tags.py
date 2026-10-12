"""One coverage line's total flex tag: employee tag + dependant tag, joined by
the single combine rule every snapshot and recompute surface shares.
"""
from __future__ import annotations

from typing import Any

from app.services.cohort_tiers import tier_key
from app.services.flex_pricing.bag import DEFAULT_FLEX_SOURCE, DependantMode
from app.services.flex_pricing.dependant_tags import (
    _dependant_tag_for_mode,
    _effective_dependant_mode,
)
from app.services.flex_pricing.price_tags import member_price_tag


def member_coverage_tag(
    *,
    source_map: dict[str, Any] | None,
    rule: str,
    pricing: dict[str, Any] | None,
    slip_idx: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    age: int | None,
    declined: bool,
    tier_category_id: str | None,
    plan_code: str | None,
    default_tier_category_id: str | None,
    default_plan: str | None,
    spouse_count: int,
    child_count: int,
    dep_profiles: list[tuple[str, int | None]] | None = None,
    dep_option_ids: dict[str, Any] | None = None,
    factor: float = 1.0,
) -> float | None:
    """Total flex drawn down for one product = employee plan tag (see
    ``member_price_tag``) + dependant tag (see ``dependant_tag``), combined by
    the single ``_combine_tags`` rule every snapshot/recompute surface shares.

    ``dep_profiles`` (per-dependant role+age) feeds ``slip_options`` pricing —
    pass it wherever the covered dependants are known so age-banded dependant
    option rows price on each dependant's own age. ``dep_option_ids``
    (``{role: category_id}``) is the elected freestanding option level per role
    (rule-4 choices). Participation changes selection only: compulsory dependant
    cover includes every eligible dependant automatically, while voluntary cover
    uses the employee's selection. In both cases the dependant charge is added to
    the employee's own flex-wallet drawdown.

    ``factor`` is the member's flex pro-ration — pass ``factor_of(employee)``
    from every call site, so a snapshot taken at election time is scaled by the
    same rule the statement recomputes with.

    Declined coverage (employee + dependants) costs no flex → None."""
    emp = member_price_tag(
        source_map=source_map, rule=rule, pricing=pricing, slip_idx=slip_idx,
        product_id=product_id, age=age, declined=declined,
        tier_category_id=tier_category_id, plan_code=plan_code,
        default_tier_category_id=default_tier_category_id, default_plan=default_plan,
    )
    if declined:
        return emp  # None — no coverage, no flex
    source = (source_map or {}).get(product_id, DEFAULT_FLEX_SOURCE)
    mode = _effective_dependant_mode(
        pricing, product_id, source, family_slip_idx,
        tier_key(tier_category_id, plan_code),
    )
    dep = _dependant_tag_for_mode(
        mode, source=source, pricing=pricing, family_slip_idx=family_slip_idx,
        product_id=product_id, tier_category_id=tier_category_id, plan_code=plan_code,
        spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
    )
    return _combine_tags(
        emp, dep, spouse_count + child_count,
        dep_applies=mode != DependantMode.none, factor=factor,
    )


def _combine_tags(
    emp: float | None,
    dep: float | None,
    covered_count: int,
    *,
    dep_applies: bool,
    factor: float = 1.0,
) -> float | None:
    """THE combine rule for a coverage line's flex tag — shared by the election
    snapshot (``member_coverage_tag``) and the benefit-statement recompute
    (``_member_flex_line``) so the two can never diverge.

    Covered dependants whose tag can't price — while dependant pricing applies —
    unprice the WHOLE tag (surfaced by the unpriced-election guard) rather than
    letting a priced employee component silently absorb $0 dependants. When
    neither component prices → None. Otherwise the components sum (an
    inapplicable one counts as $0), rounded to cents.

    ``factor`` is the member's flex pro-ration (``flex_proration.factor_of``),
    applied HERE because this is the one place every tag producer passes through.
    A pro-rated allowance with an annual price tag would leave a mid-year joiner
    holding 3/12 of the wallet and 12/12 of the cost — overdrawn before electing
    anything — so the cover charged against a wallet is scaled by the same factor
    that sized it. Applied AFTER the GST multiplier and rounded ONCE, so a
    grossed pro-rated tag and a pro-rated grossed tag cannot differ by a cent.
    Claims deliberately never pass through here: reimbursed money does not
    pro-rate."""
    if dep is None and covered_count and dep_applies:
        return None
    if emp is None and dep is None:
        return None
    return round(((emp or 0.0) + (dep or 0.0)) * factor, 2)
