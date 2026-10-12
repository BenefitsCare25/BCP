"""A member's effective coverage + leave resolved into a flex wallet balance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee
from app.models.employee_plan_override import EmployeePlanOverride
from app.models.leave_election import LeaveElection
from app.services import flex_proration
from app.services.cohort_tiers import first_category_per_product, tier_key
from app.services.coverage_resolver import employee_category_defaults, load_overrides
from app.services.flex_pricing.bag import (
    DEFAULT_FLEX_SOURCE,
    DependantMode,
    _open_band,
    _product_block,
)
from app.services.flex_pricing.context import FlexYearContext, employee_age, flex_year_context
from app.services.flex_pricing.coverage_tags import _combine_tags
from app.services.flex_pricing.dependant_profiles import (
    active_dependant_profiles,
    compulsory_dependant_category_ids,
    covered_dependant_profiles,
    dependant_age_limits,
    effective_dependant_participation,
    profile_counts,
)
from app.services.flex_pricing.dependant_tags import (
    _dependant_tag_for_mode,
    _effective_dependant_mode,
)
from app.services.flex_pricing.price_tags import member_price_tag
from app.services.voluntary_enrolment import eligible_only_product_ids


@dataclass(frozen=True)
class FlexPriceLine:
    product_id: str
    product_code: str
    tier_key: str
    plan_code: str | None
    price_tag: float | None  # total = employee plan tag + dependant tag
    # The dependant portion of ``price_tag`` (covered spouse/children), so the
    # statement can show "incl. $X dependants". None when the product has no
    # dependant pricing; 0.0 when configured but the member is Employee-Only.
    dependant_tag: float | None = None


@dataclass(frozen=True)
class FlexPriceSummary:
    wallet_amount: float | None
    currency: str | None
    age: int | None
    age_known: bool
    total_price_tag: float
    balance: float | None  # wallet - coverage spend + leave impact (None when no wallet)
    lines: list[FlexPriceLine]
    # Effective buy/sell-leave trade folded into the balance (signed: buy spends,
    # sell credits). leave_flex_amount is None when no priced leave applies.
    leave_action: str | None = None
    leave_days: float | None = None
    leave_flex_amount: float | None = None


def _effective_leave(db: Session, employee: Employee) -> LeaveElection | None:
    """The member's effective, priced buy/sell-leave trade for the year.

    Uses the shared ``latest_confirmed_leave`` selector (newest confirmed row) so
    the materialized balance and the revert path agree on which row is effective;
    an in-progress draft is shown live by the enrollment panel, not here. Returns
    the row only when it carries a snapshotted ``flex_amount``.
    """
    from app.models.leave_election import LeaveAction
    from app.services.leave_pricing_resolver import latest_confirmed_leave

    row = latest_confirmed_leave(db, employee)
    if row is None or row.action == LeaveAction.none or row.flex_amount is None:
        return None
    return row


def summarize_employee(
    db: Session, employee: Employee, *, context: FlexYearContext | None = None
) -> FlexPriceSummary | None:
    """Resolve the member's effective coverage + leave → wallet balance.

    Effective tier per product = their override if present, else the matched
    cohort default; available flex = wallet - coverage spend + leave impact (buy
    spends, sell credits). Price tags come from the placement slip by default (per
    the governing window's config), so a member is priced even with no manual matrix
    configured. Returns None only when the member has no flex wallet AND no priced
    leave AND no pricing matrix — i.e. flex doesn't apply to them.

    ``context`` is an optional prebuilt `FlexYearContext` for callers resolving
    many members of ONE year. It changes nothing about the result — it is the
    same values, computed once instead of per member.
    """
    ctx = context if context is not None else flex_year_context(
        db, employee.policy_year_id
    )
    pricing = ctx.pricing
    leave = _effective_leave(db, employee)
    has_wallet = isinstance(employee.flex_wallet_amount, (int, float))
    if pricing is None and leave is None and not has_wallet:
        return None
    age = employee_age(employee, ctx.reference_date)
    ref = ctx.reference_date
    # The member's own pro-ration, read from the wallet's stored derivation so
    # the cover charged and the allowance it is charged against are scaled by
    # ONE number. 1.0 when the scheme does not pro-rate.
    factor = flex_proration.factor_of(employee)

    # Company-wide flex config (source per product + drawdown rule) governs how each
    # line is priced. The slip indices are built whenever any product resolves to
    # the slip source (the default), so coverage prices from the slip with no matrix.
    source_map, rule = ctx.source_map, ctx.rule
    slip_idx, family_slip_idx = ctx.slip_idx, ctx.family_slip_idx

    lines: list[FlexPriceLine] = []
    total = 0.0
    if pricing is not None or slip_idx or family_slip_idx:
        defaults = employee_category_defaults(db, employee)  # {product_id: (code, plan)}
        baseline_cat = _baseline_category_ids(db, employee)
        # Categories whose dependant cover is compulsory. They auto-include every
        # active eligible dependant; pricing still draws from employee flex.
        compulsory_dep_cats = compulsory_dependant_category_ids(
            db, set(baseline_cat.values())
        )
        overrides = load_overrides(db, employee.policy_year_id, [employee.id])
        # Voluntary cover the member has not taken up is eligibility, not
        # coverage: like a decline it draws nothing until they enrol.
        not_enrolled = eligible_only_product_ids(db, employee, overrides=overrides)
        for product_id, (product_code, default_plan) in defaults.items():
            ov = overrides.get((employee.id, product_id))
            if (ov is not None and ov.declined) or product_id in not_enrolled:
                continue  # declined / not enrolled coverage costs no flex
            base_cat = baseline_cat.get(product_id)
            line = _member_flex_line(
                db, pricing=pricing, source_map=source_map, rule=rule,
                slip_idx=slip_idx, family_slip_idx=family_slip_idx,
                product_id=product_id, product_code=product_code,
                default_plan=default_plan, base_cat=base_cat, ov=ov,
                age=age, ref=ref,
                employee_id=employee.id,
                dependants_compulsory=base_cat in compulsory_dep_cats,
                factor=factor,
            )
            if line.price_tag is not None:
                total += line.price_tag
            lines.append(line)

    leave_amount = leave.flex_amount if leave else None
    wallet = employee.flex_wallet_amount
    balance = (
        (wallet - total + (leave_amount or 0.0))
        if isinstance(wallet, (int, float))
        else None
    )
    return FlexPriceSummary(
        wallet_amount=wallet,
        currency=employee.flex_currency,
        age=age,
        # "Unknown" only matters when a line's price actually turns on the age:
        # a fixed plan price (or a slip premium) was still deducted without it.
        age_known=age is not None or not any(
            _age_priced(pricing, slip_idx, line.product_id) for line in lines
        ),
        total_price_tag=total,
        balance=balance,
        lines=lines,
        leave_action=leave.action if leave else None,
        leave_days=leave.days if leave else None,
        leave_flex_amount=leave_amount,
    )


def _age_priced(
    pricing: dict[str, Any] | None,
    slip_idx: dict[str, Any] | None,
    product_id: str,
) -> bool:
    """Whether a product's member tag needs the member's age: matrix rows with
    no all-ages band, or an age-rated (voluntary life) slip tier."""
    block = _product_block(pricing, product_id) or {}
    if block.get("price_tags") and _open_band(block.get("age_bands")) is None:
        return True
    tiers = (slip_idx or {}).get(product_id)
    return isinstance(tiers, dict) and any(isinstance(v, dict) for v in tiers.values())


def _member_flex_line(
    db: Session,
    *,
    pricing: dict[str, Any] | None,
    source_map: dict[str, Any],
    rule: str,
    slip_idx: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    product_id: str,
    product_code: str,
    default_plan: str | None,
    base_cat: str | None,
    ov: EmployeePlanOverride | None,
    age: int | None,
    ref: date | None,
    employee_id: str,
    dependants_compulsory: bool,
    factor: float = 1.0,
) -> FlexPriceLine:
    """One product's effective-coverage flex line for the benefit statement:
    the member's override (or cohort default) priced through the SAME pieces as
    the election snapshot — ``member_price_tag`` + ``_dependant_tag_for_mode``
    + ``_combine_tags`` — so the statement recompute can't diverge from what
    ``member_coverage_tag`` stamped at election time."""
    if ov is not None:
        cat_id = ov.tier_category_id or base_cat
        plan_code = ov.plan_code or default_plan
        covered_ids = ov.covered_dependant_ids
        dep_option_ids = ov.dependant_option_ids
    else:
        cat_id = base_cat
        plan_code = default_plan
        covered_ids = None  # no override → Employee-Only default
        dep_option_ids = None
    key = tier_key(cat_id, plan_code)
    age_limits = dependant_age_limits(pricing, product_id)
    participation = effective_dependant_participation(
        pricing,
        product_id,
        key,
        "compulsory" if dependants_compulsory else "voluntary",
    )
    if participation == "compulsory":
        dep_profiles = active_dependant_profiles(
            db,
            employee_id,
            age_limits=age_limits,
            ref=ref,
        )
    elif participation == "voluntary":
        dep_profiles = covered_dependant_profiles(
            db,
            covered_ids,
            age_limits=age_limits,
            ref=ref,
        )
    else:
        dep_profiles = []
    spouse_count, child_count = profile_counts(dep_profiles)
    emp_tag = member_price_tag(
        source_map=source_map, rule=rule, pricing=pricing, slip_idx=slip_idx,
        product_id=product_id, age=age, declined=False,
        tier_category_id=cat_id, plan_code=plan_code,
        default_tier_category_id=base_cat, default_plan=default_plan,
    )
    src = (source_map or {}).get(product_id, DEFAULT_FLEX_SOURCE)
    # Participation controls whether the family is automatic or elected. It does
    # not change the payer: dependant charges always draw employee flex.
    mode = _effective_dependant_mode(
        pricing, product_id, src, family_slip_idx, key
    )
    dep_tag = _dependant_tag_for_mode(
        mode, source=src, pricing=pricing, family_slip_idx=family_slip_idx,
        product_id=product_id, tier_category_id=cat_id, plan_code=plan_code,
        spouse_count=spouse_count, child_count=child_count,
        dep_profiles=dep_profiles, dep_option_ids=dep_option_ids,
    )
    dep_applies = mode != DependantMode.none
    return FlexPriceLine(
        product_id=product_id,
        product_code=product_code,
        tier_key=key,
        plan_code=plan_code,
        price_tag=_combine_tags(
            emp_tag, dep_tag, spouse_count + child_count,
            dep_applies=dep_applies, factor=factor,
        ),
        # The dependant PORTION of the (pro-rated) tag, so "incl. $X dependants"
        # stays a component of the number printed beside it.
        dependant_tag=(
            None if dep_tag is None else round(dep_tag * factor, 2)
        ),
    )


def _baseline_category_ids(db: Session, employee: Employee) -> dict[str, str]:
    """``{product_id: matched_category_id}`` — the member's baseline tier per product.

    Uses the shared ``first_category_per_product`` selection so this agrees with the
    bulk-update snapshot path (and any future caller); a member matched to more than
    one category for a product must resolve to the SAME one everywhere.
    """
    from app.models.category import Category

    cat_ids = [
        m["category_id"]
        for m in (employee.matched_categories or [])
        if m.get("category_id")
    ]
    if not cat_ids:
        return {}
    product_of: dict[str, str] = {
        category_id: product_id
        for category_id, product_id in db.execute(
            select(Category.id, Category.product_id).where(
                Category.id.in_(cat_ids), Category.product_id.is_not(None)
            )
        ).all()
        if product_id is not None
    }
    return first_category_per_product(employee.matched_categories or [], product_of)
