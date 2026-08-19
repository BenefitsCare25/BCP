"""Shared dependant-coverage interpretation for category-based benefits."""
from __future__ import annotations

import re
from typing import Any

_EMPLOYEE_ONLY_TIERS = {"EO", "E", "EE", "EMPLOYEE", "EMPLOYEE ONLY"}
_VALID_DEPENDANT_MODES = {"compulsory", "voluntary"}
_NEG_DEPENDANT = re.compile(
    r"(?:\bno\b|\bnot\b|\bnon[-\s]?|\bwithout\b|\bexcl)[\w\s.,/-]{0,15}depend", re.I
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


def category_covers_dependants(
    has_dependants: bool,
    plan_assignments: dict[str, Any] | None,
    participation_detail: dict[str, Any] | None = None,
    display_name: str | None = None,
    raw_description: str | None = None,
) -> bool:
    """Best-available signal that a category extends cover to dependants.

    ``participation_detail.dependant`` is explicit. A present null means the
    broker set this category/plan to "Not covered"; legacy categories without
    the key still fall back to rates, family tiers, and extracted text.
    """
    if not has_dependants:
        return False

    detail = participation_detail if isinstance(participation_detail, dict) else {}
    if "dependant" in detail:
        return _clean_mode(detail.get("dependant")) is not None

    pa = plan_assignments if isinstance(plan_assignments, dict) else {}
    if pa.get("dependant_rate") is not None:
        return True
    if _has_dependant_tier(pa):
        return True

    text = f"{display_name or ''} {raw_description or ''}".lower()
    if "depend" not in text:
        return False
    return not _NEG_DEPENDANT.search(text)
