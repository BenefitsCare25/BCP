"""Detect benefit renames that would orphan existing claims.

`Claim.benefit_key` is a NAME STRING, not a foreign key: `claims.submit_claim`
validates it against the lowercased `name` of each `Plan.benefit_schedule` item,
and `utilization.py` buckets approved amounts by the same key. Nothing links the
two, so renaming (or deleting) a benefit line in the SOB editor silently strands
every claim that referenced it — the claim stops matching its schedule item and
its utilization bucket loses its limit.

This module answers one question for the write paths: *which* claim benefit keys
for this product would stop resolving if the schedule were replaced with these
items? The caller turns a non-empty answer into a 409 the broker can acknowledge.

Renaming is a legitimate thing to do (fixing a typo from the slip), so this is a
confirmable warning, never a hard block — the same shape as the flex coverage
guard in `flex_schemes.py`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claim import Claim


def schedule_benefit_names(items: Any) -> set[str]:
    """Lowercased benefit names in a schedule's items — the claim join key."""
    if not isinstance(items, list):
        return set()
    return {
        str(i.get("name") or "").strip().lower()
        for i in items
        if isinstance(i, dict) and str(i.get("name") or "").strip()
    }


def orphaned_benefit_keys(
    db: Session,
    *,
    policy_year_id: str,
    product_code: str | None,
    new_items: Any,
) -> list[str]:
    """Claim benefit keys for this product that the new schedule no longer has.

    Returns the ORIGINAL key spelling (not lowercased) so the message names what
    the broker will recognise. Empty when nothing would break — including the
    common case of a product with no keyed claims at all, which is every claim
    created since intake stopped asking for a benefit (they carry NULL and bucket
    at product level).
    """
    if not product_code:
        return []
    surviving = schedule_benefit_names(new_items)
    rows = db.execute(
        select(Claim.benefit_key)
        .where(
            Claim.policy_year_id == policy_year_id,
            Claim.product_code == product_code,
            Claim.benefit_key.is_not(None),
        )
        .distinct()
    ).scalars()

    orphaned: dict[str, str] = {}
    for key in rows:
        cleaned = str(key or "").strip()
        if cleaned and cleaned.lower() not in surviving:
            orphaned.setdefault(cleaned.lower(), cleaned)
    return sorted(orphaned.values())


def orphan_conflict_detail(keys: list[str], product_code: str) -> dict[str, Any]:
    """The 409 body for a rename that would strand claims."""
    listed = ", ".join(f"'{k}'" for k in keys[:5])
    if len(keys) > 5:
        listed += f" and {len(keys) - 5} more"
    return {
        "code": "orphaned_benefit_keys",
        "message": (
            f"{len(keys)} benefit(s) referenced by existing {product_code} claims "
            f"are no longer on the schedule: {listed}. Those claims would lose "
            "their utilization limit. Rename them back, or resend with "
            "acknowledge=true to proceed."
        ),
        "product_code": product_code,
        "orphaned_benefit_keys": keys,
    }
