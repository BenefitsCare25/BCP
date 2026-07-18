"""Underwriting cases — free-cover-limit sync + report amounts.

``refresh_underwriting_cases`` compares every member's (and covered
dependant's) eligible sum insured on lump-sum products against the product's
``ProductTerm.free_cover_limit`` and keeps one case per life-above-FCL:
new excesses open a *pending* case (auto-covered at FCL), resolved decisions
persist, and pending cases whose eligible SI dropped back under FCL are
removed. Pure flush — the caller owns audit + commit (mirrors matching).

Insurer listings read cases through ``load_cases`` / ``uw_amounts``:
no case → accepted = eligible, pending 0.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PolicyYear, ProductTerm, UnderwritingCase
from app.models.underwriting_case import UnderwritingStatus

# Key: (subject_id, product_id) — subject is the employee OR dependant id.
CaseMap = dict[tuple[str, str], UnderwritingCase]


def load_cases(db: Session, policy_year_id: str) -> CaseMap:
    rows = db.execute(
        select(UnderwritingCase).where(
            UnderwritingCase.policy_year_id == policy_year_id
        )
    ).scalars().all()
    return {
        ((c.employee_id or c.dependant_id or ""), c.product_id): c for c in rows
    }


def uw_amounts(case: UnderwritingCase | None, eligible: float) -> tuple[float, float]:
    """(pending, last accepted) for the report columns.

    No case → fully auto-accepted. A pending case is in force at its
    ``accepted_si`` (the FCL) with the excess pending; a decided case is in
    force at the insurer's figure with nothing pending (a declined excess is
    not "pending", it's refused — remarks carry the story).
    """
    if case is None:
        return 0.0, eligible
    accepted = min(case.accepted_si, eligible)
    if case.status == UnderwritingStatus.pending:
        return max(eligible - accepted, 0.0), accepted
    return 0.0, accepted


def free_cover_limits(db: Session, policy_year_id: str) -> dict[str, float]:
    """{product_id: FCL} for products with an explicit limit."""
    rows = db.execute(
        select(ProductTerm.product_id, ProductTerm.free_cover_limit).where(
            ProductTerm.policy_year_id == policy_year_id,
            ProductTerm.free_cover_limit.isnot(None),
        )
    ).all()
    return {pid: float(fcl) for pid, fcl in rows}


@dataclass
class RefreshResult:
    opened: int = 0
    updated: int = 0
    removed: int = 0
    open_cases: int = 0


def refresh_underwriting_cases(db: Session, policy_year: PolicyYear) -> RefreshResult:
    """Sync cases with resolved coverage. Flushes, never commits."""
    # Local import: insurer_listings imports load_cases/uw_amounts from here.
    from app.services.insurer_listings import eligible_amounts

    fcl_by_product = free_cover_limits(db, policy_year.id)
    cases = load_cases(db, policy_year.id)
    result = RefreshResult()

    eligibles = eligible_amounts(db, policy_year) if fcl_by_product else {}
    seen: set[tuple[str, str]] = set()
    for (subject_id, product_id, is_employee), eligible in eligibles.items():
        fcl = fcl_by_product.get(product_id)
        if fcl is None or eligible <= fcl:
            continue
        seen.add((subject_id, product_id))
        case = cases.get((subject_id, product_id))
        if case is None:
            db.add(UnderwritingCase(
                client_id=policy_year.client_id,
                policy_year_id=policy_year.id,
                product_id=product_id,
                employee_id=subject_id if is_employee else None,
                dependant_id=None if is_employee else subject_id,
                eligible_si=eligible,
                accepted_si=fcl,
                status=UnderwritingStatus.pending,
            ))
            result.opened += 1
        else:
            changed = False
            if case.eligible_si != eligible:
                case.eligible_si = eligible
                changed = True
            # A pending case's in-force amount tracks the FCL, so re-sync it
            # whenever the limit moves (not only when eligible changes) — else a
            # raised/lowered FCL leaves the auto-accepted figure stale. A decided
            # case keeps the insurer's figure (uw_amounts caps it at read time).
            if case.status == UnderwritingStatus.pending and case.accepted_si != fcl:
                case.accepted_si = fcl
                changed = True
            if changed:
                result.updated += 1

    # Pending cases whose life no longer exceeds the FCL are moot; decided
    # cases stay as history (uw_amounts caps them so they can't overstate).
    for key, case in cases.items():
        if key not in seen and case.status == UnderwritingStatus.pending:
            db.delete(case)
            result.removed += 1

    db.flush()
    result.open_cases = len(seen)
    return result
