"""Who counts as a covered dependant: family roles, age-eligibility windows,
per-dependant (role, age) profiles, and dependant participation.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dependant
from app.models.dependant import DEPENDANT_STATUS_ACTIVE
from app.services.flex_pricing.bag import (
    _DEP_AGE_KEY,
    DEFAULT_DEPENDANT_AGE_LIMITS,
    DependantParticipation,
    _dependant_block,
    _is_age,
)
from app.services.roster_attributes import age_from_attrs


def family_role(spouse_count: int, child_count: int) -> str | None:
    """Canonical family role from covered counts; Employee-Only (none) → None."""
    has_s, has_c = spouse_count > 0, child_count > 0
    if not has_s and not has_c:
        return None
    if has_s and not has_c:
        return "spouse"
    if has_c and not has_s:
        return "child"
    return "both"


def _overlay_age_window(out: dict[str, dict[str, int]], cfg: object) -> None:
    """Overlay a ``{role: {min?, max?}}`` config onto ``out`` in place, keeping only
    valid age bounds (an absent/invalid bound leaves the base untouched)."""
    if not isinstance(cfg, dict):
        return
    for role in out:
        win = cfg.get(role)
        if isinstance(win, dict):
            if _is_age(win.get("min")):
                out[role]["min"] = win["min"]
            if _is_age(win.get("max")):
                out[role]["max"] = win["max"]


def dependant_age_limits(
    pricing: dict[str, Any] | None, product_id: str
) -> dict[str, dict[str, int]]:
    """Per-product dependant eligibility windows (spouse/child min-max age),
    resolved most-specific-wins: hardcoded defaults, then the flex-scheme-level
    default (``meta.dependant_age_limits``, stamped onto the bag by ``get_pricing``),
    then the product's own ``dependant.age_limits`` override. Used to drop
    out-of-limit dependants from coverage + pricing."""
    out = {role: dict(win) for role, win in DEFAULT_DEPENDANT_AGE_LIMITS.items()}
    _overlay_age_window(out, (pricing or {}).get(_DEP_AGE_KEY))
    _overlay_age_window(out, _dependant_block(pricing, product_id).get("age_limits"))
    return out


def scheme_dependant_age_limits(meta: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """The scheme-wide dependant eligibility window (spouse/child min-max age): the
    hardcoded defaults overlaid with ``meta.dependant_age_limits``. Product-agnostic —
    this is the window that sizes family status / flex wallets, so membership matches
    the coverage/pricing eligibility; a product's own pricing entry can tighten it
    further via ``dependant_age_limits(pricing, product_id)``."""
    out = {role: dict(win) for role, win in DEFAULT_DEPENDANT_AGE_LIMITS.items()}
    _overlay_age_window(out, (meta or {}).get("dependant_age_limits"))
    return out


def role_age_eligible(
    role: str | None, age: int | None, limits: dict[str, dict[str, int]] | None
) -> bool:
    """Is a (role, age) within the role's eligibility window? Unknown role, unknown
    age, or no window → kept (can't prove ineligibility) — eligibility only excludes
    a dependant we can positively place outside the window.

    ``age`` is the actual age (last birthday) as of the renewal date, but the
    window bounds are age-NEXT-birthday (the canonical limit convention), so the
    comparison is done in ANB terms (``age + 1``). Rate-band lookups keep the
    actual age — slip rate tables are quoted "Based on Age Last Birthday"."""
    win = (limits or {}).get(role or "")
    if not win or age is None:
        return True
    anb = age + 1
    lo, hi = win.get("min"), win.get("max")
    return (lo is None or anb >= lo) and (hi is None or anb <= hi)


def _dependant_role_age(
    dep: Dependant, ref: date | None
) -> tuple[str, int | None] | None:
    """``(role, age)`` for one dependant — None when the relationship can't
    classify. The single classification rule shared by every profile loader so
    coverage, pricing, and the options display agree on who counts."""
    from app.services.flex_membership import classify_relationship

    av = dep.attribute_values or {}
    role = classify_relationship(av.get("relationship") or av.get("relation"))
    if role is None:
        return None
    return role, (age_from_attrs(av, ref) if ref is not None else None)


def _dependant_eligible(dep: Dependant, limits: dict[str, dict[str, int]], ref: date) -> bool:
    """Is ``dep`` within their role's age window? (See ``role_age_eligible``.)"""
    prof = _dependant_role_age(dep, ref)
    if prof is None:
        return True
    return role_age_eligible(prof[0], prof[1], limits)


def covered_dependant_profiles(
    db: Session,
    covered_dependant_ids: list[str] | None,
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> list[tuple[str, int | None]]:
    """``[(role, age)]`` per covered dependant — the per-dependant view that
    ``slip_options`` pricing needs (each dependant draws its own option rate,
    age-banded rates resolve on the DEPENDANT's age).

    Unclassifiable dependants are dropped, and so are ones no longer ACTIVE: a
    dependant the listing has since terminated stays in the stored ids but is no
    longer covered, so it must not keep drawing on the wallet (the compulsory
    path and the statement's coverage lines already count active rows only).
    Derive counts from the profiles via ``profile_counts`` so a caller loads the
    dependants once."""
    ids = [i for i in (covered_dependant_ids or []) if i]
    if not ids:
        return []
    deps = list(
        db.execute(
            select(Dependant).where(
                Dependant.id.in_(ids), Dependant.status == DEPENDANT_STATUS_ACTIVE
            )
        ).scalars().all()
    )
    return dependant_profiles_of(deps, age_limits=age_limits, ref=ref)


def dependant_profiles_of(
    dependants: list[Dependant],
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> list[tuple[str, int | None]]:
    """``covered_dependant_profiles`` over ALREADY-LOADED rows.

    Bulk paths load every selected member's dependants in one query and then
    price member by member; without this they would re-SELECT the same rows once
    per employee. Same filtering, one implementation — the DB-hitting variant
    above delegates here so the two can't drift.
    """
    deps = dependants
    if age_limits is not None and ref is not None:
        deps = [d for d in deps if _dependant_eligible(d, age_limits, ref)]
    return [p for p in (_dependant_role_age(d, ref) for d in deps) if p is not None]


def dependant_profiles_by_id(
    db: Session, employee_id: str, ref: date | None
) -> dict[str, tuple[str, int | None]]:
    """``{dependant_id: (role, age)}`` for one employee's ACTIVE dependants
    (unclassifiable relationships dropped). Feeds the options API's per-dependant
    resolved amounts for age-banded dependant option levels; per-product age
    limits are applied by the caller (``role_age_eligible``) since the map is
    built once across products."""
    out: dict[str, tuple[str, int | None]] = {}
    deps = db.execute(
        select(Dependant).where(
            Dependant.employee_id == employee_id,
            Dependant.status == "active",
        )
    ).scalars()
    for d in deps:
        prof = _dependant_role_age(d, ref)
        if prof is not None:
            out[d.id] = prof
    return out


def active_dependant_profiles(
    db: Session,
    employee_id: str,
    *,
    age_limits: dict[str, dict[str, int]] | None = None,
    ref: date | None = None,
) -> list[tuple[str, int | None]]:
    """Eligible profiles for every active dependant of an employee.

    Compulsory dependant participation controls selection (all eligible lives are
    covered automatically), not funding. These profiles therefore feed the same
    employee-wallet pricing path as a voluntary dependant selection.
    """
    profiles = list(dependant_profiles_by_id(db, employee_id, ref).values())
    if age_limits is None:
        return profiles
    return [
        profile
        for profile in profiles
        if role_age_eligible(profile[0], profile[1], age_limits)
    ]


def profile_counts(profiles: list[tuple[str, int | None]]) -> tuple[int, int]:
    """``(spouse_count, child_count)`` from dependant profiles."""
    spouse = sum(1 for role, _ in profiles if role == "spouse")
    child = sum(1 for role, _ in profiles if role == "child")
    return spouse, child


def effective_dependant_participation(
    pricing: dict[str, Any] | None,
    product_id: str,
    key: str,
    detected: str | None,
) -> str | None:
    """Resolve dependant enrolment for one exact category/plan tier.

    A saved plan-level choice wins over the placement-slip classification.
    ``none`` is an explicit removal, represented as ``None`` to every enrollment
    caller. Overrides intentionally require an exact tier key so one employee
    cohort can never change a sibling cohort by accident.
    """
    participation = _dependant_block(pricing, product_id).get("participation")
    configured = participation.get(key) if isinstance(participation, dict) else None
    if configured == DependantParticipation.none:
        return None
    if configured in (
        DependantParticipation.compulsory,
        DependantParticipation.voluntary,
    ):
        return str(configured)
    return detected if detected in ("compulsory", "voluntary") else None


def compulsory_dependant_category_ids(db: Session, cat_ids: set[str]) -> set[str]:
    """Of the given categories, those whose dependant participation is compulsory.

    Compulsory means auto-covered; it does not change the funding source. Any
    dependant price is still drawn from the employee's flex wallet.
    """
    from app.models.category import Category

    if not cat_ids:
        return set()
    out: set[str] = set()
    for cid, detail in db.execute(
        select(Category.id, Category.participation_detail).where(Category.id.in_(cat_ids))
    ).all():
        if isinstance(detail, dict) and detail.get("dependant") == "compulsory":
            out.add(cid)
    return out
