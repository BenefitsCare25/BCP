"""Shared claim predicates so dashboard counts and destination queues agree."""

from sqlalchemy import and_, func, tuple_
from sqlalchemy.sql.elements import ColumnElement

from app.models.claim import Claim


def claim_insurer_filter(
    insurer: str, placements: dict[tuple[str, str], str]
) -> ColumnElement[bool]:
    """Match canonical year-specific placement without multiplying claims."""
    matches = [
        key for key, name in placements.items() if name.casefold() == insurer.strip().casefold()
    ]
    return and_(
        Claim.claim_kind == "insured",
        tuple_(Claim.policy_year_id, func.upper(func.trim(Claim.product_code))).in_(matches),
    )
