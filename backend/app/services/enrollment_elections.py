"""Shared enrollment election core — used by BOTH the broker surface
(`api/v1/enrollments.py`) and the member portal (`api/v1/portal_enrollment.py`).

Everything here is auth-agnostic: callers own tenant/member scoping and the
audit write (broker `write_audit` vs member `write_member_audit`) + commit.
Keeping the election/options/leave/submit logic in one place is what guarantees
a member electing for themselves goes through exactly the validation and
pricing snapshots a broker-on-behalf election does.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Employee,
    Enrollment,
    EnrollmentElection,
    EnrollmentWindow,
    LeaveElection,
    LeavePolicy,
    PolicyYear,
)
from app.models.enrollment import ElectionAction, EnrollmentStatus
from app.models.enrollment_window import WindowStatus
from app.models.leave_election import LeaveAction, LeaveElectionStatus
from app.models.product import Product as ProductModel
from app.schemas.api import PlanFinancials
from app.schemas.enrollment import (
    BenefitDifferenceOut,
    CohortTierOut,
    DependantOptionChoiceOut,
    DependantOptionRoleOut,
    DependantPricingOut,
    DependantRoleOut,
    DependantTierPricingOut,
    EnrollmentElectionIn,
    EnrollmentElectionOut,
    EnrollmentOptionsOut,
    EnrollmentOut,
    EnrollmentWindowOut,
    LeaveActionStr,
    LeaveElectionIn,
    LeaveElectionOut,
    MemberLeaveOptionsOut,
    PortalEnrollmentOut,
    ProductTierSetOut,
)
from app.services import flex_proration
from app.services.cohort_tiers import (
    CohortTier,
    ProductTierSet,
    electable_tiers_for_employee,
    tier_key,
)
from app.services.coverage_resolver import employee_compulsory_product_ids
from app.services.enrollment_flex_guard import (
    assert_elections_priced,
    assert_within_wallet,
)
from app.services.enrollment_lifecycle import plan_rank
from app.services.enrollment_products import available_plan_codes, resolve_product_by_code
from app.services.enrollment_validation import (
    action_for_tier,
    assert_dependants_owned,
    assert_not_compulsory_decline,
    assert_plan_available,
    assert_product_in_scope,
    assert_valid_dependant_options,
    assert_window_accepts_edits,
    classify_action,
    resolve_electable_tier,
    validate_leave,
    window_in_period,
)
from app.services.flex_pricing_resolver import (
    DEFAULT_FLEX_SOURCE,
    FAMILY_SCHEMES,
    DependantMode,
    configured_voluntary_rates,
    covered_dependant_profiles,
    dependant_age_limits,
    dependant_option_choices,
    dependant_pricing_breakdown,
    dependant_profiles_by_id,
    effective_dependant_participation,
    employee_age,
    get_pricing,
    gst_multiplier_for,
    maybe_family_slip_index,
    maybe_slip_index,
    member_coverage_tag,
    member_price_tag,
    option_amount,
    product_premium_multiplier,
    profile_counts,
    reference_date,
    role_age_eligible,
    window_flex_config,
)
from app.services.leave_pricing_resolver import (
    leave_attribute,
    leave_flex_amount,
    leave_limits_for,
    leave_rate_for,
    leave_sell_eligible,
)
from app.services.plan_hydration import apply_gst_to_financials


def _require_window(db: Session, enrollment: Enrollment) -> EnrollmentWindow:
    window = db.get(EnrollmentWindow, enrollment.window_id)
    if window is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The enrollment window no longer exists. Reload before continuing.",
        )
    return window


def _require_policy_year(db: Session, enrollment: Enrollment) -> PolicyYear:
    policy_year = db.get(PolicyYear, enrollment.policy_year_id)
    if policy_year is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The enrollment benefit year no longer exists. Reload before continuing.",
        )
    return policy_year


def _require_employee(db: Session, enrollment: Enrollment) -> Employee:
    employee = db.get(Employee, enrollment.employee_id)
    if employee is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The enrollment member no longer exists. Reload before continuing.",
        )
    return employee


def _leave_action(value: str) -> LeaveActionStr:
    if value not in (LeaveAction.none, LeaveAction.buy, LeaveAction.sell):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The saved leave election has an invalid action.",
        )
    return cast(LeaveActionStr, value)


def _benefit_difference_out(
    difference: dict[str, str | None],
) -> BenefitDifferenceOut:
    benefit = difference.get("benefit")
    if benefit is None:
        raise ValueError("A benefit difference is missing its benefit label")
    return BenefitDifferenceOut(
        group=difference.get("group"),
        benefit=benefit,
        qualifier=difference.get("qualifier"),
        current=difference.get("current"),
        elected=difference.get("elected"),
        kind=difference.get("kind"),
    )


def open_window_for(db: Session, employee: Employee) -> EnrollmentWindow | None:
    """The open, in-period enrollment window for the employee's policy year.
    With several open windows the one closing soonest wins (the member should
    act on the most urgent deadline first)."""
    windows = db.execute(
        select(EnrollmentWindow)
        .where(
            EnrollmentWindow.policy_year_id == employee.policy_year_id,
            EnrollmentWindow.status == WindowStatus.open,
        )
        .order_by(EnrollmentWindow.closes_at)
    ).scalars().all()
    for window in windows:
        if window_in_period(window):
            return window
    return None


def member_window_for(db: Session, employee: Employee) -> EnrollmentWindow | None:
    """The open window a MEMBER may see and act in.

    `open_window_for` answers "is a period open at all"; this answers "may the
    member use it". They differ by ``member_self_service``: a broker can run a
    period broker-managed — open, with brokers electing on members' behalf and
    confirming as normal — while the portal's enrolment surface stays dark.

    **Every member-facing call site, and the broker's employee-view preview,
    must use THIS one.** Reaching for `open_window_for` re-exposes exactly the
    surface the toggle exists to hide, and does it silently: the member sees the
    "enrolment open" marker and the page loads. Broker paths keep
    `open_window_for` — hiding the portal must never hide the period from the
    people running it.
    """
    window = open_window_for(db, employee)
    if window is None or not window.member_self_service:
        return None
    return window


def find_enrollment(
    db: Session, window: EnrollmentWindow, employee: Employee
) -> Enrollment | None:
    return db.execute(
        select(Enrollment).where(
            Enrollment.window_id == window.id,
            Enrollment.employee_id == employee.id,
        )
    ).scalar_one_or_none()


def _member_safe_options(options: EnrollmentOptionsOut) -> EnrollmentOptionsOut:
    """Strip broker-facing PREMIUM figures from the enrollment options.

    `build_enrollment_options` is the broker's payload: each tier carries the
    premium rate, the annual premium and the age-banded rate table. Those are
    the same figures `member_statement.py` deliberately nulls before the portal
    ever sees a benefit statement — but the enrollment surface was calling the
    builder directly, so a member electing a plan was shown the rate the
    employer is charged for them ("Rate (per $1k SI) 454"). Two surfaces
    disagreeing about what a member may see is how a leak survives review.

    What SURVIVES is what a member actually decides on:
      * ``sum_insured`` — how much they'd be covered for, and
      * ``price_tag`` (untouched, on the tier itself) — what the change costs
        THEM out of their own flex wallet.

    ``num_employees`` and ``basis`` go too: the first is the slip's cohort
    headcount and the second the group rating basis, both broker aggregates
    about the scheme rather than facts about this member. Nothing renders them,
    but they were still in the JSON the member's browser received.

    A premium is what the company pays the insurer. It is not a price the
    member can act on, and showing it next to a wallet figure invites reading
    one as the other.
    """

    def scrub(fin: PlanFinancials | None) -> PlanFinancials | None:
        if fin is None:
            return None
        return fin.model_copy(
            update={
                "premium_rate": None,
                "annual_premium": None,
                "rate_basis": None,
                "rate_tiers": None,
                "dependant_rate": None,
                "estimated_annual_earnings": None,
                "voluntary_rates": None,
                # Broker aggregates about the COHORT, not this member: the
                # slip's stated headcount and the basis it was rated on.
                "num_employees": None,
                "basis": None,
                # The badge only means anything beside a premium figure.
                "gst_included": False,
            }
        )

    return options.model_copy(
        update={
            "products": [
                p.model_copy(
                    update={
                        "tiers": [
                            t.model_copy(update={"financials": scrub(t.financials)})
                            for t in p.tiers
                        ]
                    }
                )
                for p in options.products
            ]
        }
    )


def build_portal_enrollment(
    db: Session,
    employee: Employee | None,
    *,
    enrollment: Enrollment | None = None,
) -> PortalEnrollmentOut:
    """The member enrollment payload (window + own session + options) — served
    to the member on /portal/enrollment and mirrored read-only by the broker's
    employee-view preview (which never materializes an enrollment row).

    Premium figures are scrubbed here rather than in the API layer so BOTH
    consumers — the member endpoint and the preview — are covered by one gate,
    the same shape as `build_member_statement`."""
    if employee is None:
        return PortalEnrollmentOut()
    window = member_window_for(db, employee)
    if window is None:
        return PortalEnrollmentOut()
    enr = enrollment or find_enrollment(db, window, employee)
    return PortalEnrollmentOut(
        window=EnrollmentWindowOut.model_validate(window),
        enrollment=enrollment_detail(db, enr) if enr is not None else None,
        options=_member_safe_options(
            build_enrollment_options(
                db, employee, window, employee.policy_year_id,
                enrollment_id=enr.id if enr is not None else None,
            )
        ),
    )


def enrollment_detail(db: Session, enr: Enrollment) -> EnrollmentOut:
    """Full enrollment payload (elections + leave + compulsory locks)."""
    emp = db.get(Employee, enr.employee_id)
    elections = db.execute(
        select(EnrollmentElection)
        .where(EnrollmentElection.enrollment_id == enr.id)
        .order_by(EnrollmentElection.product_code)
    ).scalars().all()
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enr.id)
    ).scalar_one_or_none()

    # Resolve compulsory product codes for this employee so the UI can lock them.
    compulsory_codes: list[str] = []
    if emp:
        compulsory_ids = employee_compulsory_product_ids(db, emp)
        if compulsory_ids:
            compulsory_codes = list(
                db.execute(
                    select(ProductModel.code).where(ProductModel.id.in_(compulsory_ids))
                ).scalars().all()
            )

    return EnrollmentOut(
        id=enr.id,
        window_id=enr.window_id,
        policy_year_id=enr.policy_year_id,
        employee_id=enr.employee_id,
        staff_id=emp.staff_id if emp else "?",
        employee_name=emp.employee_name if emp else None,
        status=enr.status,
        baseline_snapshot=enr.baseline_snapshot,
        submitted_at=enr.submitted_at,
        confirmed_at=enr.confirmed_at,
        elections=[EnrollmentElectionOut.model_validate(e) for e in elections],
        leave=LeaveElectionOut.model_validate(leave) if leave else None,
        compulsory_product_codes=compulsory_codes,
    )


def build_enrollment_options(
    db: Session,
    employee: Employee | None,
    window: EnrollmentWindow | None,
    policy_year_id: str,
    *,
    enrollment_id: str | None = None,
) -> EnrollmentOptionsOut:
    """Per-product electable tiers for this member, scoped to their cohort.

    Each product lists only the baseline tier plus the voluntary sibling tiers
    of the same cohort, direction-labelled (upgrade/downgrade). Takes the
    employee/window directly (not an Enrollment) so the portal preview can
    build options without materializing an enrollment row.
    """
    tier_sets = electable_tiers_for_employee(db, employee) if employee else {}
    # Flex price tags (wallet cost of each tier), priced per the window's config:
    # source per product (slip premium vs portal matrix) + drawdown rule (full plan
    # tag vs only the upgrade/downgrade difference vs the member's default plan).
    flex_active = bool(
        window
        and window.uses_flex
        and employee
        and employee.flex_wallet_amount is not None
        and bool(employee.flex_currency)
    )
    pricing = get_pricing(db, policy_year_id)
    source_map, rule = window_flex_config(window) if window else ({}, "full")
    slip_idx = maybe_slip_index(db, policy_year_id, source_map)
    family_slip_idx = maybe_family_slip_index(db, policy_year_id, source_map)
    ref = reference_date(db, policy_year_id)
    age = employee_age(employee, ref) if employee is not None else None
    # {dependant_id: (role, age)} — lets age-banded dependant option levels show
    # a concrete amount per THIS member's dependants instead of "priced at save".
    dep_profiles_by_id = (
        dependant_profiles_by_id(db, employee.id, ref) if employee is not None else {}
    )
    # Product NAMES for the member surface, resolved once rather than per tier
    # set. Loaded by id (the ids came from this year's own categories), so a
    # firm-library product with client_id NULL resolves the same as a
    # company-owned one.
    product_names: dict[str, str] = {}
    pids = [ts.product_id for ts in tier_sets.values() if ts.product_id]
    if pids:
        product_names = {
            p.id: p.display_name
            for p in db.scalars(
                select(ProductModel).where(ProductModel.id.in_(pids))
            ).all()
        }
    def tier_dependant_participation(ts: ProductTierSet, tier: CohortTier) -> str | None:
        return effective_dependant_participation(
            pricing,
            ts.product_id,
            tier_key(tier.tier_category_id, tier.plan_code),
            tier.dependant_participation or ts.dependant_participation,
        )

    products = [
        ProductTierSetOut(
            product_id=ts.product_id,
            product_code=ts.product_code,
            product_name=product_names.get(ts.product_id),
            employee_participation=ts.employee_participation,
            dependant_participation=next(
                (
                    tier_dependant_participation(ts, tier)
                    for tier in ts.tiers
                    if tier.is_baseline
                ),
                None,
            ),
            baseline_tier_category_id=ts.baseline_tier_category_id,
            baseline_plan_code=ts.baseline_plan_code,
            allow_plan_change=ts.allow_plan_change,
            can_decline=ts.can_decline,
            tiers=[
                CohortTierOut(
                    key=tier_key(t.tier_category_id, t.plan_code),
                    tier_category_id=t.tier_category_id,
                    plan_code=t.plan_code,
                    label=t.label,
                    participation=t.participation,
                    dependant_participation=tier_dependant_participation(ts, t),
                    direction=t.direction,
                    is_baseline=t.is_baseline,
                    is_current=t.is_current,
                    # Gross the displayed PREMIUM by the product's own GST only
                    # (product_premium_multiplier — never the flex-scheme default),
                    # so it matches the benefit statement's premium for this product.
                    # The flex price tag beside it may still gross by the scheme
                    # default (member_price_tag) — that's the wallet cost, not the
                    # premium.
                    financials=(
                        apply_gst_to_financials(
                            t.financials,
                            product_premium_multiplier(pricing, ts.product_id),
                        )
                        if t.financials is not None
                        else None
                    ),
                    price_tag=(
                        member_price_tag(
                            source_map=source_map, rule=rule, pricing=pricing,
                            slip_idx=slip_idx, product_id=ts.product_id, age=age,
                            declined=False,
                            tier_category_id=t.tier_category_id, plan_code=t.plan_code,
                            default_tier_category_id=ts.baseline_tier_category_id,
                            default_plan=ts.baseline_plan_code,
                        )
                        if flex_active
                        else None
                    ),
                    # Entitlement, not premium — member-safe, and the
                    # whole point of the tier list. `_member_safe_options`
                    # scrubs `financials` only, so these survive to the
                    # portal untouched.
                    differences=[
                        _benefit_difference_out(d) for d in t.differences
                    ],
                    differences_total=t.differences_total,
                )
                for t in ts.tiers
            ],
            dependant=_dependant_pricing_out(
                pricing,
                family_slip_idx,
                source_map,
                ts,
                dep_profiles_by_id,
                expose_amounts=flex_active,
            ),
        )
        for ts in sorted(tier_sets.values(), key=lambda s: s.product_code)
    ]
    leave_policy = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == policy_year_id)
    ).scalar_one_or_none()
    return EnrollmentOptionsOut(
        enrollment_id=enrollment_id,
        products=products,
        flex_wallet=employee.flex_wallet_amount if flex_active and employee else None,
        flex_proration=(
            flex_proration.proration_line(employee) if flex_active and employee else None
        ),
        flex_currency=employee.flex_currency if flex_active and employee else None,
        member_age=age,
        member_leave_rate=(
            leave_rate_for(leave_policy, employee) if flex_active and employee else None
        ),
        leave=_member_leave_options(leave_policy, employee),
        flex_drawdown_rule=rule,
    )


def _member_leave_options(
    policy: LeavePolicy | None, employee: Employee | None
) -> MemberLeaveOptionsOut | None:
    """The leave bounds this member trades within, or None when the year has no
    leave policy (nothing to trade).

    The maxima are the RESOLVED per-tier caps, not the policy's global fields —
    the UI states a limit and the server enforces one, so they must be the same
    number (`validate_leave` reads the same resolver)."""
    if policy is None:
        return None
    limits = leave_limits_for(policy, employee)
    return MemberLeaveOptionsOut(
        allow_buy=policy.allow_buy,
        allow_sell=policy.allow_sell,
        min_buy_days=policy.min_buy_days,
        max_buy_days=limits.max_buy_days,
        min_sell_days=policy.min_sell_days,
        max_sell_days=limits.max_sell_days,
        increment_days=policy.increment_days,
        # No employee (a preview built without one) can't be shown as ineligible.
        sell_eligible=leave_sell_eligible(employee) if employee else True,
        rate_attribute=leave_attribute(policy),
        rate_value=limits.tier_value,
        limits_from_tier=limits.from_tier,
    )


def _dependant_pricing_out(
    pricing: dict[str, Any] | None,
    family_slip_idx: dict[str, Any] | None,
    source_map: dict[str, Any] | None,
    ts: ProductTierSet,
    dep_profiles_by_id: dict[str, tuple[str, int | None]] | None = None,
    *,
    expose_amounts: bool = True,
) -> DependantPricingOut | None:
    """The product's dependant pricing, priced PER tier (the amount differs per
    plan), with the scheme's display labels. None when dependant pricing doesn't
    apply (effective mode ``none``).

    The source defaults to the slip (``DEFAULT_FLEX_SOURCE``) and the mode is the
    EFFECTIVE mode from ``dependant_pricing_breakdown`` (which applies the slip
    default), so this options preview matches what ``apply_elections`` snapshots and
    the benefit statement recomputes — an unconfigured slip product with EO/ES/EC/EF
    rates surfaces its family pricing here instead of reading as 'none'."""
    source = (source_map or {}).get(ts.product_id, DEFAULT_FLEX_SOURCE)
    mode = DependantMode.none
    scheme: str | None = None
    by_tier: dict[str, DependantTierPricingOut] = {}
    for t in ts.tiers:
        bd = dependant_pricing_breakdown(
            pricing=pricing, family_slip_idx=family_slip_idx, source=source,
            product_id=ts.product_id, tier_category_id=t.tier_category_id,
            plan_code=t.plan_code,
        )
        if bd["mode"] != DependantMode.none:
            mode = bd["mode"]
        scheme = bd["scheme"] or scheme
        labels = FAMILY_SCHEMES.get(bd["scheme"] or "", {})
        by_tier[tier_key(t.tier_category_id, t.plan_code)] = DependantTierPricingOut(
            mode=bd["mode"],
            per_pax_rate=bd["per_pax_rate"] if expose_amounts else None,
            family=[
                DependantRoleOut(
                    role=f["role"],
                    label=labels.get(f["role"], f["role"]),
                    amount=f["amount"] if expose_amounts else None,
                )
                for f in bd["family"]
            ],
        )
    if mode == DependantMode.none:
        return None
    # Rule-4 choices are product-level (attached identically to every employee
    # tier), so resolve them ONCE off the baseline tier instead of per tier.
    choices = dependant_option_choices(
        family_slip_idx, ts.product_id,
        tier_key(ts.baseline_tier_category_id, ts.baseline_plan_code),
    )
    return DependantPricingOut(
        mode=mode, scheme=scheme, by_tier=by_tier,
        option_choices=_option_choices_out(
            choices, dep_profiles_by_id or {},
            dependant_age_limits(pricing, ts.product_id),
            gst_multiplier_for(pricing, ts.product_id),
            configured_voluntary_rates(pricing, ts.product_id),
            expose_amounts=expose_amounts,
        ),
    )


def _option_choices_out(
    choices: dict[str, list[dict[str, Any]]],
    dep_profiles_by_id: dict[str, tuple[str, int | None]],
    age_limits: dict[str, dict[str, int]],
    gst_mult: float = 1.0,
    voluntary_rates: list[Any] | None = None,
    *,
    expose_amounts: bool = True,
) -> list[DependantOptionRoleOut]:
    """Freestanding dependant option LEVELS as API output, with each level's flex
    amount resolved per the member's own dependants (age-banded levels price on
    each dependant's age — None when the date of birth is unknown). Dependants
    outside the product's eligibility window are excluded — the same filter the
    save path applies, so the preview never shows a price the snapshot drops.

    Amounts are grossed by the product's flex GST multiplier so the level a member
    sees equals what the wallet is drawn (``_dependant_tag_for_mode`` grosses the
    same way); ``sum_insured`` is coverage, not premium, so it is left raw."""
    def _g(amount: float | None) -> float | None:
        return round(amount * gst_mult, 2) if amount is not None and gst_mult != 1.0 else amount

    out: list[DependantOptionRoleOut] = []
    for role in ("spouse", "child"):
        rows = choices.get(role) or []
        if not rows:
            continue
        role_deps = {
            dep_id: prof_age
            for dep_id, (prof_role, prof_age) in dep_profiles_by_id.items()
            if prof_role == role and role_age_eligible(prof_role, prof_age, age_limits)
        }
        out.append(DependantOptionRoleOut(
            role=role,
            choices=[
                DependantOptionChoiceOut(
                    category_id=c["category_id"],
                    label=c["label"],
                    sum_insured=c.get("sum_insured"),
                    amount=(
                        _g(option_amount(c.get("spec"), None, voluntary_rates))
                        if expose_amounts
                        else None
                    ),
                    amounts_by_dependant=(
                        {
                            dep_id: _g(option_amount(c.get("spec"), dep_age, voluntary_rates))
                            for dep_id, dep_age in role_deps.items()
                        }
                        if expose_amounts
                        else {}
                    ),
                )
                for c in rows
            ],
        ))
    return out


def _prepare_enrollment_edit(enr: Enrollment) -> None:
    """Move broker-editable records back to draft and reject deemed records."""
    if enr.status == EnrollmentStatus.deemed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A deemed enrollment is finalized and cannot be edited.",
        )
    if enr.status in (EnrollmentStatus.submitted, EnrollmentStatus.confirmed):
        enr.status = EnrollmentStatus.in_progress
        enr.submitted_at = None
        enr.submitted_by = None
    if enr.confirmed_at is not None or enr.confirmed_by is not None:
        enr.confirmed_at = None
        enr.confirmed_by = None


def lock_enrollment(db: Session, enr: Enrollment) -> Enrollment:
    """Serialize mutations to one member's enrollment lifecycle."""
    return db.execute(
        select(Enrollment)
        .where(Enrollment.id == enr.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def apply_elections(
    db: Session,
    enr: Enrollment,
    elections: list[EnrollmentElectionIn],
    *,
    prepare_edit: bool = True,
) -> None:
    """Validate + upsert plan elections onto an enrollment. Flushes but does
    NOT audit or commit — the caller owns actor attribution."""
    window = _require_window(db, enr)
    assert_window_accepts_edits(window)
    if prepare_edit:
        _prepare_enrollment_edit(enr)
    if not window.allow_plan_change:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan changes are disabled for this window.")
    py = _require_policy_year(db, enr)
    employee = _require_employee(db, enr)
    baseline_products = (enr.baseline_snapshot or {}).get("products", {})
    rank_cache: dict[str, dict[str, int]] = {}
    avail_cache: dict[str, set[str]] = {}
    compulsory_ids = employee_compulsory_product_ids(db, employee)
    # Cohort-scoped electable tiers per product — restricts the election to the
    # member's own cohort instead of every plan of the product. Validating an
    # election needs tier IDENTITY only, so skip the schedule-difference pass:
    # it queries and flattens every offered plan's schedule, and this path
    # discards the result.
    tier_sets = (
        electable_tiers_for_employee(db, employee, include_differences=False)
    )
    # Flex price-tag snapshot inputs, resolved once: the matrix, the member's age,
    # and the window's source/rule config (slip vs matrix; full vs on-change).
    flex_active = bool(
        window.uses_flex
        and employee.flex_wallet_amount is not None
        and employee.flex_currency
    )
    pricing = get_pricing(db, py.id)
    source_map, drawdown_rule = window_flex_config(window)
    slip_idx = maybe_slip_index(db, py.id, source_map)
    family_slip_idx = maybe_family_slip_index(db, py.id, source_map)
    ref = reference_date(db, py.id)
    member_age = employee_age(employee, ref)

    for item in elections:
        product = resolve_product_by_code(db, py, item.product_code)
        if product is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Product '{item.product_code}' is not configured in this policy year.",
            )
        assert_product_in_scope(window, item.product_code)
        if item.declined:
            assert_not_compulsory_decline(item.product_code, compulsory_ids, product.id)
        if window.allow_dependant_changes and item.covered_dependant_ids:
            assert_dependants_owned(db, employee, item.covered_dependant_ids)
        elif item.covered_dependant_ids and not window.allow_dependant_changes:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Dependant changes are disabled for this window."
            )

        previous = (baseline_products.get(item.product_code) or {}).get("plan_code")
        tier_set = tier_sets.get(item.product_code)
        tier: CohortTier | None = None
        elected_plan_code: str | None = None
        elected_tier_id: str | None = None
        if item.declined:
            action = ElectionAction.decline
        elif tier_set is not None:
            # Cohort-scoped path: validate + resolve to a tier in the member's cohort.
            tier = resolve_electable_tier(tier_set, item.plan_code, item.tier_category_id)
            elected_plan_code = tier.plan_code
            elected_tier_id = tier.tier_category_id
            # A non-baseline tier's plan must be a configured Plan — a cohort tier
            # can name a plan_code with no Plan row (category/plan drift); don't
            # write a dangling override. Keeping the baseline is always allowed.
            if elected_plan_code and not tier.is_baseline:
                if product.id not in avail_cache:
                    avail_cache[product.id] = available_plan_codes(db, py.id, product.id)
                assert_plan_available(elected_plan_code, avail_cache[product.id], item.product_code)
            if product.id not in rank_cache:
                rank_cache[product.id] = plan_rank(db, py.id, product.id)
            action = action_for_tier(previous, tier, rank_cache[product.id])
        else:
            # Legacy fallback for products without resolvable cohort tiers
            # (e.g. unmatched members / pre-participation data).
            if product.id not in avail_cache:
                avail_cache[product.id] = available_plan_codes(db, py.id, product.id)
            assert_plan_available(item.plan_code, avail_cache[product.id], item.product_code)
            elected_plan_code = item.plan_code
            elected_tier_id = item.tier_category_id
            if product.id not in rank_cache:
                rank_cache[product.id] = plan_rank(db, py.id, product.id)
            action = classify_action(
                previous, item.plan_code, item.declined, rank_cache[product.id]
            )

        existing = db.execute(
            select(EnrollmentElection).where(
                EnrollmentElection.enrollment_id == enr.id,
                EnrollmentElection.product_id == product.id,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = EnrollmentElection(
                enrollment_id=enr.id,
                policy_year_id=enr.policy_year_id,
                client_id=enr.client_id,
                product_id=product.id,
                product_code=product.code,
            )
            db.add(existing)
        # An elected dependant option level must be one of the product's real
        # electable choices for the resolved tier — the same set the options
        # API exposed — so a bad id 422s at save instead of silently never
        # pricing. (Declined elections can't carry levels; schema rejects.)
        if item.dependant_option_ids:
            assert_valid_dependant_options(
                item.dependant_option_ids,
                dependant_option_choices(
                    family_slip_idx, product.id,
                    tier_key(elected_tier_id, elected_plan_code),
                ),
            )

        dep_limits = dependant_age_limits(pricing, product.id)
        dependant_participation = (
            None
            if item.declined
            else effective_dependant_participation(
                pricing,
                product.id,
                tier_key(elected_tier_id, elected_plan_code),
                (
                    tier.dependant_participation or tier_set.dependant_participation
                    if tier is not None and tier_set is not None
                    else tier_set.dependant_participation
                    if tier_set is not None
                    else "voluntary"
                ) or "voluntary",
            )
        )
        if dependant_participation == "compulsory":
            all_profiles = dependant_profiles_by_id(db, employee.id, ref)
            eligible_profiles = {
                dep_id: profile
                for dep_id, profile in all_profiles.items()
                if role_age_eligible(profile[0], profile[1], dep_limits)
            }
            resolved_covered_ids: list[str] | None = sorted(eligible_profiles)
            dep_profiles = list(eligible_profiles.values())
        elif dependant_participation == "voluntary":
            resolved_covered_ids = (
                None if item.declined else item.covered_dependant_ids
            )
            dep_profiles = covered_dependant_profiles(
                db,
                resolved_covered_ids,
                age_limits=dep_limits,
                ref=ref,
            )
        else:
            resolved_covered_ids = None
            dep_profiles = []

        existing.previous_plan_code = previous
        existing.elected_plan_code = elected_plan_code
        existing.tier_category_id = None if item.declined else elected_tier_id
        existing.action = action
        # Compulsory means every active eligible dependant is covered
        # automatically. An omitted or partial client list must not waive the
        # dependant charge drawn from the employee's flex wallet.
        existing.covered_dependant_ids = resolved_covered_ids
        existing.dependant_option_ids = (
            None
            if item.declined or dependant_participation is None
            else (item.dependant_option_ids or None)
        )
        # Snapshot the total flex draw-down (employee plan tag + dependant tag) for
        # the resolved tier under the window's config (None when declined). For an
        # "on_change" window the employee portion is the difference vs the member's
        # default plan; the dependant portion is the incremental cost of the covered
        # dependants. Stays stable if pricing changes later.
        # Per-dependant profiles (role + age) so slip-option dependant rows —
        # which stick to the elected employee plan — price on each dependant's
        # own age; counts derive from the same load. The product's eligibility
        # windows apply here exactly as they do on every recompute surface
        # (bulk, revert, benefit statement) so the snapshot can't diverge.
        spouse_count, child_count = profile_counts(dep_profiles)
        existing.flex_price_tag = (
            member_coverage_tag(
                source_map=source_map,
                rule=drawdown_rule,
                pricing=pricing,
                slip_idx=slip_idx,
                family_slip_idx=family_slip_idx,
                product_id=product.id,
                age=member_age,
                declined=item.declined,
                tier_category_id=elected_tier_id,
                plan_code=elected_plan_code,
                default_tier_category_id=(
                    tier_set.baseline_tier_category_id if tier_set else None
                ),
                default_plan=tier_set.baseline_plan_code if tier_set else None,
                spouse_count=spouse_count,
                child_count=child_count,
                dep_profiles=dep_profiles,
                dep_option_ids=existing.dependant_option_ids,
                factor=flex_proration.factor_of(employee),
            )
            if flex_active
            else None
        )
        existing.notes = item.notes

    if enr.status == EnrollmentStatus.not_started:
        enr.status = EnrollmentStatus.in_progress
    db.flush()


def apply_leave(
    db: Session,
    enr: Enrollment,
    body: LeaveElectionIn,
    *,
    prepare_edit: bool = True,
) -> LeaveElection:
    """Validate + upsert the buy/sell-leave election. Flushes but does NOT
    audit or commit."""
    window = _require_window(db, enr)
    assert_window_accepts_edits(window)
    if prepare_edit:
        _prepare_enrollment_edit(enr)
    if not window.allow_leave:
        raise HTTPException(status.HTTP_409_CONFLICT, "Leave trading is disabled for this window.")
    policy = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == enr.policy_year_id)
    ).scalar_one_or_none()
    employee = _require_employee(db, enr)
    # Load the employee FIRST: the day caps are per grade/designation tier, so
    # validating without them would enforce the company default for everyone.
    validate_leave(policy, body.action, body.days, employee)
    # Per-member sell eligibility from the roster flag ("Eligible to Sell
    # Leave"); absent = eligible. Shared with the insurer reports.
    if (
        body.action == LeaveAction.sell
        and not leave_sell_eligible(employee)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This member is not eligible to sell leave.",
        )

    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enr.id)
    ).scalar_one_or_none()
    if leave is None:
        leave = LeaveElection(
            enrollment_id=enr.id,
            policy_year_id=enr.policy_year_id,
            client_id=enr.client_id,
            employee_id=enr.employee_id,
        )
        db.add(leave)
    leave.action = body.action
    leave.days = body.days
    # An edit returns the election to draft: finalization must flow through
    # submit→confirm (or window-close deeming), never go live straight from an edit.
    leave.status = LeaveElectionStatus.draft
    # Snapshot the signed flex-wallet impact (buy spends, sell credits) from the
    # member's leave rate so the available-balance recompute stays stable if the
    # policy's rates change later.
    rate = leave_rate_for(policy, employee)
    leave.flex_amount = (
        leave_flex_amount(body.action, body.days, rate)
        if bool(
            window.uses_flex
            and employee.flex_wallet_amount is not None
            and employee.flex_currency
        )
        else None
    )
    if enr.status == EnrollmentStatus.not_started:
        enr.status = EnrollmentStatus.in_progress
    db.flush()
    return leave


def revalidate_enrollment(db: Session, enr: Enrollment) -> None:
    """Re-run saved choices against current plans, eligibility, and pricing."""
    elections = db.execute(
        select(EnrollmentElection).where(EnrollmentElection.enrollment_id == enr.id)
    ).scalars().all()
    if elections:
        saved_tags = {e.id: e.flex_price_tag for e in elections}
        apply_elections(
            db,
            enr,
            [
                EnrollmentElectionIn(
                    product_code=e.product_code,
                    plan_code=e.elected_plan_code,
                    tier_category_id=e.tier_category_id,
                    declined=e.action == ElectionAction.decline,
                    covered_dependant_ids=e.covered_dependant_ids,
                    dependant_option_ids=e.dependant_option_ids,
                    notes=e.notes,
                )
                for e in elections
            ],
            prepare_edit=False,
        )
        # Price tags are submission snapshots. Structural revalidation must not
        # rewrite an already-reviewed wallet debit during confirmation/close.
        for election in elections:
            election.flex_price_tag = saved_tags[election.id]
    leave = db.execute(
        select(LeaveElection).where(LeaveElection.enrollment_id == enr.id)
    ).scalar_one_or_none()
    if leave is not None:
        apply_leave(
            db,
            enr,
            LeaveElectionIn(action=_leave_action(leave.action), days=leave.days),
            prepare_edit=False,
        )


def perform_submit(
    db: Session, enr: Enrollment, *, acknowledge: bool, actor_id: str | None
) -> None:
    """Mark an enrollment submitted after the shared flex guards. Flushes but
    does NOT audit or commit."""
    if enr.status in (EnrollmentStatus.confirmed, EnrollmentStatus.deemed):
        raise HTTPException(status.HTTP_409_CONFLICT, "Enrollment is already finalized.")
    window = _require_window(db, enr)
    assert_window_accepts_edits(window)
    # Flex guards: an overdrawn wallet blocks (unless the window allows
    # overdrafts); changed-but-unpriced elections need explicit acknowledgment.
    assert_within_wallet(db, enr, window)
    assert_elections_priced(db, enr, window, acknowledge=acknowledge)
    enr.status = EnrollmentStatus.submitted
    enr.submitted_at = datetime.now(UTC)
    enr.submitted_by = actor_id
    db.flush()
