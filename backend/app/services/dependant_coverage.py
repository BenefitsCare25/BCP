"""Shared dependant-coverage interpretation for category-based benefits."""
from __future__ import annotations

import re
from typing import Any

_EMPLOYEE_ONLY_TIERS = {"EO", "E", "EE", "EMPLOYEE", "EMPLOYEE ONLY"}
_VALID_DEPENDANT_MODES = {"compulsory", "voluntary"}
_NEG_DEPENDANT = re.compile(
    r"(?:\bno\b|\bnot\b|\bnon[-\s]?|\bwithout\b|\bexcl)[\w\s.,/-]{0,15}depend", re.I
)
# "All Eligible Dependants on Voluntary basis" / "Voluntary - Dependents".
_VOLUNTARY_DEPENDANT = re.compile(
    r"depend\w*[\w\s]{0,20}\bvoluntary\b|\bvoluntary\b[\s\-:]{0,5}depend", re.I
)


def _clean_mode(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in _VALID_DEPENDANT_MODES else None


def _has_dependant_tier(plan_assignments: dict[str, Any]) -> bool:
    for tier_field in ("rate_tiers", "tier_counts"):
        tiers = plan_assignments.get(tier_field)
        if isinstance(tiers, dict) and any(
            str(k).strip().upper() not in _EMPLOYEE_ONLY_TIERS for k in tiers
        ):
            return True
    return False


def category_dependant_mode(
    has_dependants: bool,
    plan_assignments: dict[str, Any] | None,
    participation_detail: dict[str, Any] | None = None,
    display_name: str | None = None,
    raw_description: str | None = None,
    *,
    legacy_product_default: bool = False,
) -> str | None:
    """How dependants join this category: ``compulsory``, ``voluntary`` or None.

    ``participation_detail.dependant`` is explicit. A present null means the
    broker set this category/plan to "Not covered"; legacy categories without
    the key still fall back to the slip's own wording, rates, family tiers and
    the old product-level dependant flag.
    """
    if not has_dependants:
        return None

    detail = participation_detail if isinstance(participation_detail, dict) else {}
    if "dependant" in detail:
        return _clean_mode(detail.get("dependant"))

    text = f"{display_name or ''} {raw_description or ''}".lower()
    # The slip's own "Voluntary" wording decides the MODE; rates and family
    # tiers only prove dependants are covered at all. An exclusion phrase
    # ("excluding dependants above 70") never outranks hard rate data.
    voluntary = bool(_VOLUNTARY_DEPENDANT.search(text))
    pa = plan_assignments if isinstance(plan_assignments, dict) else {}
    if pa.get("dependant_rate") is not None or _has_dependant_tier(pa):
        return "voluntary" if voluntary else "compulsory"
    if "depend" in text:
        if _NEG_DEPENDANT.search(text):
            return None
        return "voluntary" if voluntary else "compulsory"
    return "compulsory" if legacy_product_default else None


def category_covers_dependants(
    has_dependants: bool,
    plan_assignments: dict[str, Any] | None,
    participation_detail: dict[str, Any] | None = None,
    display_name: str | None = None,
    raw_description: str | None = None,
    *,
    legacy_product_default: bool = False,
) -> bool:
    """Dependants are covered BY DEFAULT only when their cover is compulsory.

    Voluntary dependant cover makes them eligible; a dependant is covered once
    enrolled, which the caller reads from the override's
    ``covered_dependant_ids`` (the same list flex pricing already prices).
    """
    return (
        category_dependant_mode(
            has_dependants,
            plan_assignments,
            participation_detail,
            display_name,
            raw_description,
            legacy_product_default=legacy_product_default,
        )
        == "compulsory"
    )


def slip_participation_detail(
    detail: dict[str, Any],
    *,
    has_dependants: bool,
    plan_assignments: dict[str, Any] | None,
    display_name: str | None,
    raw_description: str | None,
) -> dict[str, Any]:
    """A slip row's participation detail with the dependant mode made explicit.

    A Participation cell usually states the employee's mode only
    ("Compulsory"). Stored as ``dependant: None`` that read as the broker's
    "Not covered", which outranks everything else — so "Directors and
    Eligible Dependents" priced EO/ES/EC/EF covered no dependants at all. A
    cell that is silent on dependants leaves the decision to the slip's other
    evidence (the row's wording and its family-tier rates), resolved here once
    so every reader sees the same stored answer.
    """
    out = dict(detail)
    if _clean_mode(out.get("dependant")) is None:
        out["dependant"] = category_dependant_mode(
            has_dependants, plan_assignments, None, display_name, raw_description
        )
    return out


def per_head_dependant_rate(
    plan_assignments: dict[str, Any], dependant_mode: str | None
) -> dict[str, Any]:
    """Price each covered dependant at a row's single per-head rate.

    "Directors and Eligible Dependents · headcount 8 · rate 710 · premium
    5,680" prices every insured person — director or spouse — at 710. Left
    without a ``dependant_rate``, ``member_premium`` refuses to price a member
    with dependants rather than understate them. A slip that prices dependants
    separately (a "Dependents" row, or EO/ES/EF tiers) keeps its own figures.
    """
    pa = dict(plan_assignments)
    if (
        dependant_mode in _VALID_DEPENDANT_MODES
        and pa.get("rate_basis") in ("flat", "per_member")
        and isinstance(pa.get("premium_rate"), (int, float))
        and not isinstance(pa.get("premium_rate"), bool)
        and pa["premium_rate"] > 0
        and pa.get("dependant_rate") is None
        and not pa.get("rate_tiers")
    ):
        pa["dependant_rate"] = pa["premium_rate"]
    return pa


def has_member_cover_eligibility_answer(answers: Any) -> bool:
    if not isinstance(answers, dict):
        return False
    eligibility = answers.get("eligibility")
    return (
        isinstance(eligibility, dict)
        and "member_cover_eligibility" in eligibility
    )
