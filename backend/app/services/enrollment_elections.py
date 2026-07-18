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
from app.schemas.enrollment import (
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
    LeaveElectionIn,
    LeaveElectionOut,
    PortalEnrollmentOut,
    ProductTierSetOut,
)
from app.services.cohort_tiers import electable_tiers_for_employee, tier_key
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
    covered_dependant_profiles,
    dependant_age_limits,
    dependant_option_choices,
    dependant_pricing_breakdown,
    dependant_profiles_by_id,
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
    leave_flex_amount,
    leave_rate_for,
    leave_sell_eligible,
)
from app.services.plan_hydration import apply_gst_to_financials


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


def find_enrollment(
    db: Session, window: EnrollmentWindow, employee: Employee
) -> Enrollment | None:
    return db.execute(
        select(Enrollment).where(
            Enrollment.window_id == window.id,
            Enrollment.employee_id == employee.id,
        )
    ).scalar_one_or_none()


def build_portal_enrollment(
    db: Session,
    employee: Employee | None,
    *,
    enrollment: Enrollment | None = None,
) -> PortalEnrollmentOut:
    """The member enrollment payload (window + own session + options) — served
    to the member on /portal/enrollment and mirrored read-only by the broker's
    employee-view preview (which never materializes an enrollment row)."""
    if employee is None:
        return PortalEnrollmentOut()
    window = open_window_for(db, employee)
    if window is None:
        return PortalEnrollmentOut()
    enr = enrollment or find_enrollment(db, window, employee)
    return PortalEnrollmentOut(
        window=EnrollmentWindowOut.model_validate(window),
        enrollment=enrollment_detail(db, enr) if enr is not None else None,
        options=build_enrollment_options(
            db, employee, window, employee.policy_year_id,
            enrollment_id=enr.id if enr is not None else None,
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
    products = [
        ProductTierSetOut(
            product_id=ts.product_id,
            product_code=ts.product_code,
            employee_participation=ts.employee_participation,
            dependant_participation=ts.dependant_participation,
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
                    direction=t.direction,
                    is_baseline=t.is_baseline,
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
                    price_tag=member_price_tag(
                        source_map=source_map, rule=rule, pricing=pricing,
                        slip_idx=slip_idx, product_id=ts.product_id, age=age,
                        declined=False,
                        tier_category_id=t.tier_category_id, plan_code=t.plan_code,
                        default_tier_category_id=ts.baseline_tier_category_id,
                        default_plan=ts.baseline_plan_code,
                    ),
                )
                for t in ts.tiers
            ],
            dependant=_dependant_pricing_out(
                pricing, family_slip_idx, source_map, ts, dep_profiles_by_id
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
        flex_wallet=employee.flex_wallet_amount if employee else None,
        flex_currency=employee.flex_currency if employee else None,
        member_age=age,
        member_leave_rate=leave_rate_for(leave_policy, employee) if employee else None,
        flex_drawdown_rule=rule,
    )


def _dependant_pricing_out(
    pricing, family_slip_idx, source_map, ts, dep_profiles_by_id=None
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
            per_pax_rate=bd["per_pax_rate"],
            family=[
                DependantRoleOut(
                    role=f["role"], label=labels.get(f["role"], f["role"]), amount=f["amount"]
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
        ),
    )


def _option_choices_out(
    choices: dict, dep_profiles_by_id: dict, age_limits: dict, gst_mult: float = 1.0
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
                    amount=_g(option_amount(c.get("spec"), None)),
                    amounts_by_dependant={
                        dep_id: _g(option_amount(c.get("spec"), dep_age))
                        for dep_id, dep_age in role_deps.items()
                    },
                )
                for c in rows
            ],
        ))
    return out


def apply_elections(
    db: Session, enr: Enrollment, elections: list[EnrollmentElectionIn]
) -> None:
    """Validate + upsert plan elections onto an enrollment. Flushes but does
    NOT audit or commit — the caller owns actor attribution."""
    window = db.get(EnrollmentWindow, enr.window_id)
    assert_window_accepts_edits(window)
    if not window.allow_plan_change:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan changes are disabled for this window.")
    py = db.get(PolicyYear, enr.policy_year_id)
    employee = db.get(Employee, enr.employee_id)
    baseline_products = (enr.baseline_snapshot or {}).get("products", {})
    rank_cache: dict[str, dict[str, int]] = {}
    avail_cache: dict[str, set[str]] = {}
    compulsory_ids = employee_compulsory_product_ids(db, employee) if employee else set()
    # Cohort-scoped electable tiers per product — restricts the election to the
    # member's own cohort instead of every plan of the product.
    tier_sets = electable_tiers_for_employee(db, employee) if employee else {}
    # Flex price-tag snapshot inputs, resolved once: the matrix, the member's age,
    # and the window's source/rule config (slip vs matrix; full vs on-change).
    pricing = get_pricing(db, py.id)
    source_map, drawdown_rule = window_flex_config(window)
    slip_idx = maybe_slip_index(db, py.id, source_map)
    family_slip_idx = maybe_family_slip_index(db, py.id, source_map)
    ref = reference_date(db, py.id)
    member_age = employee_age(employee, ref) if employee else None

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

        existing.previous_plan_code = previous
        existing.elected_plan_code = elected_plan_code
        existing.tier_category_id = None if item.declined else elected_tier_id
        existing.action = action
        existing.covered_dependant_ids = item.covered_dependant_ids
        existing.dependant_option_ids = (
            None if item.declined else (item.dependant_option_ids or None)
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
        dep_profiles = covered_dependant_profiles(
            db, item.covered_dependant_ids,
            age_limits=dependant_age_limits(pricing, product.id), ref=ref,
        )
        spouse_count, child_count = profile_counts(dep_profiles)
        existing.flex_price_tag = member_coverage_tag(
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
            default_tier_category_id=tier_set.baseline_tier_category_id if tier_set else None,
            default_plan=tier_set.baseline_plan_code if tier_set else None,
            spouse_count=spouse_count,
            child_count=child_count,
            dep_profiles=dep_profiles,
            dep_option_ids=existing.dependant_option_ids,
            # Compulsory dependant cover is employer-funded: covered, but it
            # draws no member flex and can't block the tag as "unpriced".
            dependants_compulsory=(
                tier_set is not None
                and tier_set.dependant_participation == "compulsory"
            ),
        )
        existing.notes = item.notes

    if enr.status == EnrollmentStatus.not_started:
        enr.status = EnrollmentStatus.in_progress
    db.flush()


def apply_leave(db: Session, enr: Enrollment, body: LeaveElectionIn) -> LeaveElection:
    """Validate + upsert the buy/sell-leave election. Flushes but does NOT
    audit or commit."""
    window = db.get(EnrollmentWindow, enr.window_id)
    assert_window_accepts_edits(window)
    if not window.allow_leave:
        raise HTTPException(status.HTTP_409_CONFLICT, "Leave trading is disabled for this window.")
    policy = db.execute(
        select(LeavePolicy).where(LeavePolicy.policy_year_id == enr.policy_year_id)
    ).scalar_one_or_none()
    validate_leave(policy, body.action, body.days)
    employee = db.get(Employee, enr.employee_id)
    # Per-member sell eligibility from the roster flag ("Eligible to Sell
    # Leave"); absent = eligible. Shared with the insurer reports.
    if (
        body.action == LeaveAction.sell
        and employee is not None
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
    rate = leave_rate_for(policy, employee) if employee else None
    leave.flex_amount = leave_flex_amount(body.action, body.days, rate)
    if enr.status == EnrollmentStatus.not_started:
        enr.status = EnrollmentStatus.in_progress
    db.flush()
    return leave


def perform_submit(
    db: Session, enr: Enrollment, *, acknowledge: bool, actor_id: str | None
) -> None:
    """Mark an enrollment submitted after the shared flex guards. Flushes but
    does NOT audit or commit."""
    if enr.status in (EnrollmentStatus.confirmed, EnrollmentStatus.deemed):
        raise HTTPException(status.HTTP_409_CONFLICT, "Enrollment is already finalized.")
    window = db.get(EnrollmentWindow, enr.window_id)
    assert_window_accepts_edits(window)
    # Flex guards: an overdrawn wallet blocks (unless the window allows
    # overdrafts); changed-but-unpriced elections need explicit acknowledgment.
    assert_within_wallet(db, enr, window)
    assert_elections_priced(db, enr, acknowledge=acknowledge)
    enr.status = EnrollmentStatus.submitted
    enr.submitted_at = datetime.now(UTC)
    enr.submitted_by = actor_id
    db.flush()
