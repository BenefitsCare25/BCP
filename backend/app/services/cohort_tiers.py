"""Cohort-tier resolution for enrollment elections.

An employee matches exactly one (compulsory) ``Category`` per product — their
baseline tier. A placement slip also defines *alternative* tiers for the same
cohort as sibling ``Category`` rows that share the cohort identity but differ in
plan and participation (e.g. GTL "GCEO" has a compulsory Plan 1 plus voluntary
Plan 10 / Plan 17; GPA has "(Option 1/2/3)" sharing one plan_code). When the slip
marks those siblings *voluntary*, the member may elect them.

This module groups a product's categories by cohort and returns the electable
tiers for a member — scoped to their cohort and labelled upgrade/downgrade — so
the election dropdown stops listing every tier of every cohort. Direction is read
from the slip's Participation column first (the authoritative signal), then falls
back to sum-insured, then to plan-code ordering.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, Plan
from app.models.category import Category
from app.schemas.api import PlanFinancials
from app.services.matching_engine import category_insured_entities
from app.services.plan_hydration import (
    basis_amount,
    member_age,
    member_financials,
)
from app.services.tier_differences import (
    MAX_DIFFERENCES,
    differences_from_rows,
    flatten_schedule,
    has_rows,
)

logger = logging.getLogger(__name__)

# A trailing "(Option 2)" / "(Plan B)" / "(Tier 3)" distinguishes tiers within a
# cohort and must be stripped to group them; a "(Job category: 99)" is
# cohort-defining and must be kept — so only these tier-marker parentheticals go.
_TIER_SUFFIX = re.compile(r"\s*\((?:option|plan|tier)\b[^)]*\)\s*$", re.IGNORECASE)
_PLAN_PREFIX = re.compile(r"^\s*plan\s+\S+\s*[:\-]\s*", re.IGNORECASE)
_TIER_MARKER = re.compile(r"\((?:option|plan|tier)\b[^)]*\)", re.IGNORECASE)


def cohort_key(raw_description: str) -> str:
    """Identity shared by a cohort's tiers (tier markers + plan prefixes removed)."""
    s = _TIER_SUFFIX.sub("", raw_description or "")
    s = _PLAN_PREFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _tier_label(cat: Category) -> str:
    """Human label for a tier — the distinguishing marker, else the plan code."""
    marker = _TIER_MARKER.search(cat.raw_description or "")
    if marker:
        return marker.group(0).strip("()").strip()
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    code = pa.get("plan_code")
    if code:
        return f"Plan {code}"
    return cat.display_name


def _detail(cat: Category) -> dict:
    return cat.participation_detail if isinstance(cat.participation_detail, dict) else {}


def _employee_mode(cat: Category) -> str | None:
    return _detail(cat).get("employee") or cat.participation_model


def _plan_code(cat: Category) -> str | None:
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    code = pa.get("plan_code")
    return str(code) if code not in (None, "") else None


def _insured_key(cat: Category) -> frozenset[str]:
    """Normalized insured-entity SET from the category's Insured column.

    Multi-entity slips (WICA-style: one block per subsidiary) repeat the same
    category names under different legal entities; the insured entities are what
    keep "Non-Manual Staffs @ CDL" and "Non-Manual Staffs @ Le Grove" apart.

    Shares `category_insured_entities` with the matching engine so cohort
    splitting and the match gate can't disagree — the previous local
    normalization only collapsed whitespace and case, so "Pte. Ltd." and
    "Pte Ltd" split a cohort the gate treated as one entity.
    """
    return category_insured_entities(cat)


def _same_insured(a: Category, b: Category) -> bool:
    """Same insured entity — with a blank on either side acting as a wildcard,
    so manually-created categories (no insured recorded) keep grouping with
    their slip-parsed siblings."""
    ia, ib = _insured_key(a), _insured_key(b)
    return not ia or not ib or ia == ib


def _is_dependant_scope(cat: Category) -> bool:
    """True for categories that cover dependants standalone (GPA "Spouse
    (Option N)" rows, VDL dependants-sheet categories). They price dependant
    cover and must never appear as employee election tiers."""
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    return pa.get("member_scope") == "dependant"


def _sum_insured(cat: Category) -> float | None:
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    si = pa.get("sum_insured")
    return float(si) if isinstance(si, (int, float)) else None


def _basis(cat: Category) -> float | None:
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    return basis_amount(pa)


def _plan_order(code: str | None) -> tuple[int, float | str]:
    """Sort key for a plan code. GHS-style 'U0x'/'D0x' carry direction in the
    prefix (U above the base, D below); numeric codes sort numerically."""
    if not code:
        return (1, "")
    head = code[0].upper()
    if head in ("D", "U"):
        bias = -1 if head == "D" else 1
        rest = code[1:]
    else:
        bias = 0
        rest = code
    try:
        return (bias, float(re.sub(r"[^0-9.]", "", rest) or 0))
    except ValueError:
        return (bias, code)


def _direction_code(baseline: Category, code: str) -> str:
    """Direction of a bare plan code relative to the baseline (by plan ordering).

    A tie returns ``"unknown"`` — the codes don't order and there's no SI to
    compare, so the caller falls back to the plan-rank heuristic rather than
    asserting the tiers are identical coverage."""
    bo, to = _plan_order(_plan_code(baseline)), _plan_order(code)
    if to > bo:
        return "upgrade"
    if to < bo:
        return "downgrade"
    return "unknown"


def _direction(baseline: Category, tier: Category) -> str:
    """Direction of ``tier`` relative to ``baseline``: upgrade / downgrade / same.

    Slip-stated direction wins (it's the only reliable signal when tiers share a
    sum insured, as GTL does); then sum insured; then plan-code ordering.
    """
    stated = _detail(tier).get("direction")
    if stated == "upgrade":
        return "upgrade"
    if stated == "downgrade":
        return "downgrade"
    # 'both' / unstated → fall through to financial / code ordering.
    # Per-member ``basis`` (the sum assured the MEMBER holds) is the true tier
    # signal: group ``sum_insured`` is num_employees * basis, computed once on the
    # compulsory tier and copied to its voluntary siblings, so it's identical
    # across a cohort even when the per-member coverage differs. Compare basis
    # first, then group SI; EQUAL on a known figure → "same" (don't let a higher
    # plan-code number fabricate an "upgrade" for identical coverage).
    base_b, tier_b = _basis(baseline), _basis(tier)
    if base_b is not None and tier_b is not None:
        if base_b == tier_b:
            return "same"
        return "upgrade" if tier_b > base_b else "downgrade"
    base_si, tier_si = _sum_insured(baseline), _sum_insured(tier)
    if base_si is not None and tier_si is not None:
        if base_si == tier_si:
            return "same"
        return "upgrade" if tier_si > base_si else "downgrade"
    bo, to = _plan_order(_plan_code(baseline)), _plan_order(_plan_code(tier))
    if to > bo:
        return "upgrade"
    if to < bo:
        return "downgrade"
    # No SI to compare and the plan codes don't order → genuinely indeterminate.
    # "unknown" (not "same") so the action layer keeps its plan-rank fallback and
    # a real change like SILVER→GOLD still registers as up/down.
    return "unknown"


@dataclass(frozen=True)
class CohortTier:
    tier_category_id: str
    plan_code: str | None
    label: str
    participation: str | None  # 'compulsory' | 'voluntary'
    direction: str  # 'upgrade'|'downgrade'|'same'|'unknown' (rel. to baseline)
    is_baseline: bool
    financials: PlanFinancials | None
    # The tier the member ACTUALLY HOLDS right now — the cohort default unless a
    # standing ``EmployeePlanOverride`` moved them off it (a prior window's
    # confirmed upgrade). Distinct from ``is_baseline`` on purpose: the baseline
    # is a property of the COHORT (which category matched, what the flex grid
    # prices, what direction is measured from) while this is a property of the
    # MEMBER. They differ for anyone holding an override, and conflating them
    # labelled a plan the member had already left "your current plan" and
    # diffed every alternative against it.
    is_current: bool = False
    # Rows on which this tier's schedule differs from the BASELINE tier's —
    # what a member actually gives up or gains by switching. Empty for the
    # baseline itself (it is the reference) and for products whose plans
    # share one schedule and differ only in sum insured (all the life ones).
    differences: tuple[dict[str, str | None], ...] = ()
    # The true count before `MAX_DIFFERENCES` slicing, so the UI can say what
    # it is not showing rather than silently under-reporting a change.
    differences_total: int = 0


@dataclass(frozen=True)
class ProductTierSet:
    product_id: str
    product_code: str
    employee_participation: str | None
    dependant_participation: str | None
    baseline_tier_category_id: str
    baseline_plan_code: str | None
    allow_plan_change: bool  # cohort offers more than the baseline tier
    can_decline: bool  # baseline participation is not compulsory
    tiers: list[CohortTier]


def _matched_category_ids(employee: Employee) -> list[str]:
    return [
        m["category_id"]
        for m in (employee.matched_categories or [])
        if m.get("category_id")
    ]


def _sibling_tiers(
    baseline: Category,
    siblings: list[Category],
    age: int | None = None,
    attrs: dict | None = None,
) -> list[CohortTier]:
    """Tier objects for a cohort's sibling categories (baseline + voluntary).

    ``age`` age-bands the premium of voluntary life tiers (else it's None and the
    flat per_1000 / parsed figures are used)."""
    seen: set[tuple[str | None, str]] = set()
    tiers: list[CohortTier] = []
    for c in siblings:
        is_base = c.id == baseline.id
        label = _tier_label(c)
        plan = _plan_code(c)
        dedupe = (plan, label)
        if not is_base and dedupe in seen:
            continue
        seen.add(dedupe)
        tiers.append(
            CohortTier(
                tier_category_id=c.id,
                plan_code=plan,
                label=label,
                participation=_employee_mode(c),
                direction="same" if is_base else _direction(baseline, c),
                is_baseline=is_base,
                financials=member_financials(c.plan_assignments, age, attrs)
                if isinstance(c.plan_assignments, dict)
                else None,
            )
        )
    return tiers


def _unclaimed_plan_tiers(
    baseline: Category,
    product_cats: list[Category],
    siblings: list[Category],
    tiers: list[CohortTier],
    plan_codes: set[str],
) -> list[CohortTier]:
    """Product plans no *other* cohort claims — alternate tiers for this cohort
    (single-category-multi-plan products whose tiers are Plan rows, not categories)."""
    sibling_ids = {c.id for c in siblings}
    cohort_codes = {t.plan_code for t in tiers if t.plan_code}
    foreign_codes = {_plan_code(c) for c in product_cats if c.id not in sibling_ids}
    extra: list[CohortTier] = []
    for code in sorted(plan_codes):
        if code in cohort_codes or code in foreign_codes:
            continue
        extra.append(
            CohortTier(
                tier_category_id=baseline.id,
                plan_code=code,
                label=f"Plan {code}",
                participation=_employee_mode(baseline),
                direction=_direction_code(baseline, code),
                is_baseline=False,
                financials=None,
            )
        )
    return extra


def _build_tier_set(
    baseline: Category,
    product_cats: list[Category],
    plan_codes: set[str],
    product_code: str,
    age: int | None = None,
    attrs: dict | None = None,
) -> ProductTierSet:
    """Assemble one product's electable, direction-ordered tier set for a member."""
    key = cohort_key(baseline.raw_description)
    # Dependant-scope categories are excluded from the employee tier list, but
    # stay in ``product_cats`` so their plan codes remain claimed and can't
    # resurface as bogus "unclaimed plan" tiers.
    siblings = [
        c
        for c in product_cats
        if cohort_key(c.raw_description) == key
        and _same_insured(c, baseline)
        and not _is_dependant_scope(c)
    ]
    dependant_part = next(
        (_detail(c).get("dependant") for c in siblings if _detail(c).get("dependant")),
        None,
    )
    tiers = _sibling_tiers(baseline, siblings, age, attrs)
    # Unclaimed product plans are a fallback for single-category-multi-plan
    # products (the slip expressed tiers as Plan rows). When the slip ALREADY
    # enumerated this cohort's alternatives as sibling categories, that
    # enumeration is authoritative — don't append heuristic plan tiers with
    # guessed direction and no financials on top of it.
    slip_enumerated = any(not t.is_baseline for t in tiers)
    if not slip_enumerated:
        tiers.extend(
            _unclaimed_plan_tiers(baseline, product_cats, siblings, tiers, plan_codes)
        )

    # Order: baseline, then upgrades (richer first), then same, then downgrades.
    rank = {"upgrade": 0, "same": 1, "downgrade": 2}
    tiers.sort(
        key=lambda t: (0 if t.is_baseline else 1, rank.get(t.direction, 1), -_si_of(t), t.label)
    )
    emp_part = _employee_mode(baseline)
    return ProductTierSet(
        product_id=baseline.product_id or "",
        product_code=product_code,
        employee_participation=emp_part,
        dependant_participation=dependant_part,
        baseline_tier_category_id=baseline.id,
        baseline_plan_code=_plan_code(baseline),
        allow_plan_change=len(tiers) > 1,
        can_decline=emp_part != "compulsory",
        tiers=tiers,
    )


def electable_tiers_for_employee(
    db: Session, employee: Employee, *, include_differences: bool = True
) -> dict[str, ProductTierSet]:
    """``{product_code: ProductTierSet}`` of the member's electable cohort tiers.

    One entry per product the member is matched to. Each set lists the baseline
    tier plus the voluntary sibling tiers of the same cohort, direction-labelled.

    ``include_differences=False`` skips the schedule diff pass, which needs a
    query and a full flatten of every offered plan's schedule. The WRITE path
    (``apply_elections``) only needs tier identity to validate an election, so
    it would pay for dozens of 69-row schedule walks per save and discard them.
    """
    from app.models.product import Product

    matched_ids = _matched_category_ids(employee)
    if not matched_ids:
        return {}
    baselines = list(
        db.execute(
            select(Category).where(
                Category.id.in_(matched_ids), Category.product_id.is_not(None)
            )
        ).scalars()
    )
    if not baselines:
        return {}

    product_ids = {c.product_id for c in baselines}
    # All candidate tier categories across the matched products, in one query.
    by_product: dict[str, list[Category]] = {}
    for c in db.execute(
        select(Category).where(
            Category.policy_year_id == employee.policy_year_id,
            Category.product_id.in_(product_ids),
        )
    ).scalars():
        by_product.setdefault(c.product_id, []).append(c)

    code_by_pid = dict(
        db.execute(
            select(Product.id, Product.code).where(Product.id.in_(product_ids))
        ).all()
    )

    # Configured plan codes per product, scoped to THIS policy year so a product
    # shared across years can't leak another year's plans into the tier list.
    plan_codes_by_pid: dict[str, set[str]] = {}
    for pid_val, code_val in db.execute(
        select(Plan.product_id, Plan.code).where(
            Plan.product_id.in_(product_ids),
            Plan.policy_year_id == employee.policy_year_id,
        )
    ).all():
        plan_codes_by_pid.setdefault(pid_val, set()).add(str(code_val))

    # Member's age (as of the policy year start) drives voluntary life-tier premiums.
    age = member_age(db, employee)

    out: dict[str, ProductTierSet] = {}
    for baseline in baselines:
        # Defensive: a matching rule generated from a dependant-scope category
        # ("Spouse (Option 1)") should never claim an employee, but if it does,
        # don't build an employee tier set from dependant pricing.
        if _is_dependant_scope(baseline):
            continue
        pid = baseline.product_id
        assert pid is not None
        product_code = code_by_pid.get(pid, baseline.display_name)
        out[product_code] = _build_tier_set(
            baseline,
            by_product.get(pid, []),
            plan_codes_by_pid.get(pid, set()),
            product_code,
            age,
            # The member's roster attributes, so a salary-multiple sum insured
            # resolves to THEIR figure. Without it every such tier reported the
            # cohort's aggregate as the member's cover.
            employee.attribute_values,
        )
    out = _mark_current_tiers(db, employee, out)
    if not include_differences:
        return out
    return _attach_differences(db, employee.policy_year_id, out)


def _mark_current_tiers(
    db: Session, employee: Employee, sets: dict[str, ProductTierSet]
) -> dict[str, ProductTierSet]:
    """Flag the tier each product's member actually holds today.

    Effective coverage is "category default + sparse override", resolvable ONLY
    through ``coverage_resolver`` — so the cohort baseline is the member's
    current plan just until someone elects off it. ``enrollment_lifecycle``
    already pre-elects through overrides, so without this the enrollment page
    pre-selected plan 2 while calling plan 1 "your current plan".

    A DECLINED override leaves no tier current: the member holds nothing here,
    and naming one of the offers "current" would be worse than naming none.
    """
    from app.services.coverage_resolver import load_overrides

    overrides = load_overrides(db, employee.policy_year_id, [employee.id])
    out: dict[str, ProductTierSet] = {}
    for code, ts in sets.items():
        ov = overrides.get((employee.id, ts.product_id))
        held_idx = _held_tier_index(ts, ov)
        out[code] = replace(
            ts,
            tiers=[replace(t, is_current=(i == held_idx)) for i, t in enumerate(ts.tiers)],
        )
    return out


def _held_tier_index(ts: ProductTierSet, ov: Any) -> int | None:
    """Index of the tier the member holds, or None (declined / unresolvable)."""
    if ov is None:
        return next((i for i, t in enumerate(ts.tiers) if t.is_baseline), None)
    if ov.declined:
        return None
    # A plan code can repeat across tiers (GPA's "(Option N)" share one), so the
    # (category, plan) PAIR identifies a tier — the same rule the options API
    # and `resolve_electable_tier` use.
    held = ov.plan_code or ts.baseline_plan_code
    tier_cat = ov.tier_category_id
    if tier_cat:
        for i, t in enumerate(ts.tiers):
            if t.tier_category_id == tier_cat and (t.plan_code or None) == (held or None):
                return i
        for i, t in enumerate(ts.tiers):
            if t.tier_category_id == tier_cat:
                return i
    for i, t in enumerate(ts.tiers):
        if (t.plan_code or None) == (held or None):
            return i
    # An override for a plan no longer in this cohort is an ORPHAN (re-matching
    # strands them; they're inert and surfaced separately for reconciliation).
    # Fall back to the cohort default rather than claiming the member holds
    # nothing.
    return next((i for i, t in enumerate(ts.tiers) if t.is_baseline), None)


def _attach_differences(
    db: Session, policy_year_id: str | None, sets: dict[str, ProductTierSet]
) -> dict[str, ProductTierSet]:
    """Fill in each non-baseline tier's schedule difference from the baseline.

    A POST-PASS, deliberately, rather than work done inside ``_build_tier_set``:
    the schedules are only needed for the plans this member is actually
    OFFERED — two or three per product — while the tier builder runs before
    that set is known. Loading them alongside `plan_codes_by_pid` would pull
    every plan's schedule for every matched product, which on CDL means all 28
    GPA schedules and all 22 GTL ones to render three rows.

    Only the member path calls this. The config-level tier set (the flex
    pricing grid) has no member and no baseline to compare against.
    """
    wanted: set[tuple[str, str]] = set()
    for ts in sets.values():
        for t in ts.tiers:
            if t.plan_code:
                wanted.add((ts.product_id, str(t.plan_code)))
    if not wanted:
        return sets

    schedules: dict[tuple[str, str], Any] = {}
    for pid, code, sched in db.execute(
        select(Plan.product_id, Plan.code, Plan.benefit_schedule).where(
            Plan.policy_year_id == policy_year_id,
            Plan.product_id.in_({p for p, _ in wanted}),
        )
    ).all():
        schedules[(pid, str(code))] = sched

    out: dict[str, ProductTierSet] = {}
    for code, ts in sets.items():
        # Anchored on the tier the member HOLDS, not the cohort default — "if
        # you switch" has to be measured from where they actually are. They are
        # the same tier for everyone without a standing override.
        baseline = next(
            (t for t in ts.tiers if t.is_current),
            next((t for t in ts.tiers if t.is_baseline), None),
        )
        base_sched = (
            schedules.get((ts.product_id, str(baseline.plan_code)))
            if baseline and baseline.plan_code
            else None
        )
        # A baseline with no schedule on record can't anchor a comparison. That
        # is a real, documented state — an unmapped plan code gets NO schedule
        # (`resolve_plan_schedule` logs and returns []), so `schedules.get` is
        # None for it. Diffing against it reported every alternative's benefit
        # as newly GAINED, and the reverse case reported every baseline benefit
        # as lost, which the UI prints as "Not covered" — a config gap stated to
        # the member as a fact about their cover.
        if not has_rows(base_sched):
            if any(t.plan_code and not t.is_baseline for t in ts.tiers):
                logger.warning(
                    "Product %r: baseline plan %r has no benefit schedule, so no "
                    "tier differences can be shown for its alternatives.",
                    code,
                    baseline.plan_code if baseline else None,
                )
            out[code] = ts
            continue
        base_rows = flatten_schedule(base_sched)
        tiers = []
        for t in ts.tiers:
            if t is baseline or not t.plan_code:
                tiers.append(t)
                continue
            elected = schedules.get((ts.product_id, str(t.plan_code)))
            if not has_rows(elected):
                logger.warning(
                    "Product %r: plan %r has no benefit schedule, so its "
                    "differences from the current plan are unknown.",
                    code,
                    t.plan_code,
                )
                tiers.append(t)
                continue
            diffs = differences_from_rows(base_rows, flatten_schedule(elected))
            tiers.append(
                replace(
                    t,
                    differences=tuple(diffs[:MAX_DIFFERENCES]),
                    differences_total=len(diffs),
                )
            )
        out[code] = replace(ts, tiers=tiers)
    return out


def _si_of(t: CohortTier) -> float:
    """Sum insured for sorting; a real 0.0 stays 0.0, missing financials → 0.0."""
    if t.financials and t.financials.sum_insured is not None:
        return t.financials.sum_insured
    return 0.0


def tier_key(tier_category_id: str | None, plan_code: str | None) -> str:
    """Stable identity for a tier, matching the enrollment options API ``key``.

    The price-tag matrix and the election dropdown both key on this exact string
    so config and elections line up 1:1.
    """
    return f"{tier_category_id}::{plan_code or ''}"


def first_category_per_product(
    matched_categories: list[dict] | None, product_of: dict[str, str]
) -> dict[str, str]:
    """``{product_id: category_id}`` — the FIRST matched category per product.

    Stable by ``matched_categories`` order. Shared by every "baseline tier"
    lookup (flex pricing resolver + bulk update) so a member whose roster matches
    more than one category for a product resolves to the SAME baseline everywhere
    — otherwise a snapshotted price tag and its live recompute would disagree.
    ``product_of`` maps each candidate ``category_id`` to its ``product_id``.
    """
    out: dict[str, str] = {}
    for m in matched_categories or []:
        cid = m.get("category_id")
        pid = product_of.get(cid) if cid else None
        if pid and pid not in out:
            out[pid] = cid
    return out


def list_product_tiers(
    db: Session, policy_year_id: str
) -> dict[str, ProductTierSet]:
    """``{product_code: ProductTierSet}`` of EVERY electable tier in a product.

    Not employee-scoped — it unions the tiers across all cohorts of each product,
    so the price-tag config grid can list every tier a member could ever elect.
    Reuses ``_build_tier_set`` (one baseline per cohort) so keys/labels match the
    per-member options exactly.
    """
    from app.models.product import Product

    cats = list(
        db.execute(
            select(Category).where(
                Category.policy_year_id == policy_year_id,
                Category.product_id.is_not(None),
            )
        ).scalars()
    )
    if not cats:
        return {}
    by_product: dict[str, list[Category]] = {}
    for c in cats:
        by_product.setdefault(c.product_id, []).append(c)

    product_ids = list(by_product)
    code_by_pid = dict(
        db.execute(
            select(Product.id, Product.code).where(Product.id.in_(product_ids))
        ).all()
    )
    plan_codes_by_pid: dict[str, set[str]] = {}
    for pid_val, code_val in db.execute(
        select(Plan.product_id, Plan.code).where(
            Plan.product_id.in_(product_ids),
            Plan.policy_year_id == policy_year_id,
        )
    ).all():
        plan_codes_by_pid.setdefault(pid_val, set()).add(str(code_val))

    out: dict[str, ProductTierSet] = {}
    for pid, product_cats in by_product.items():
        product_code = code_by_pid.get(pid, product_cats[0].display_name)
        # One baseline per cohort (prefer the compulsory category), union their tiers.
        seen_keys: set[str] = set()
        merged: list[CohortTier] = []
        baseline_for_set = product_cats[0]
        # One canonical tier set per cohort (reuses the per-member tier logic so
        # config keys can't drift from election keys), unioned + deduped by key.
        for members in _group_by_cohort(product_cats).values():
            baseline = _pick_baseline(members)
            ts = _build_tier_set(
                baseline, product_cats, plan_codes_by_pid.get(pid, set()), product_code
            )
            for t in ts.tiers:
                k = tier_key(t.tier_category_id, t.plan_code)
                if k not in seen_keys:
                    seen_keys.add(k)
                    merged.append(t)
        out[product_code] = ProductTierSet(
            product_id=pid,
            product_code=product_code,
            employee_participation=_employee_mode(baseline_for_set),
            dependant_participation=None,
            baseline_tier_category_id=baseline_for_set.id,
            baseline_plan_code=_plan_code(baseline_for_set),
            allow_plan_change=len(merged) > 1,
            can_decline=False,
            tiers=merged,
        )
    return out


def _group_by_cohort(cats: list[Category]) -> dict[tuple[str, str], list[Category]]:
    """Cohorts are scoped per insured entity as well as description — a
    multi-subsidiary slip repeats category names per entity block and those must
    not merge into one cohort."""
    groups: dict[tuple[str, str], list[Category]] = {}
    for c in cats:
        groups.setdefault(
            (cohort_key(c.raw_description), _insured_key(c)), []
        ).append(c)
    return groups


def _pick_baseline(members: list[Category]) -> Category:
    """The cohort's baseline: the compulsory category, else the first."""
    for c in members:
        if _employee_mode(c) == "compulsory":
            return c
    return members[0]
