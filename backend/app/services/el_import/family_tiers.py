"""Dependant cover stated by a family tier instead of the dependant's own row.

Some listings (WUXI) list a dependant's own row only for per-life products (GP,
specialist, dental) and state hospital cover through the employee's family tier
(EO/ES/EC/EF) alone: the dependant row's GHS and GMM columns are blank. Every
reader of such a block — the review's headcounts, the tier check and the cover
written on apply — must take the tier as the statement of who is covered.
"""
from __future__ import annotations

from app.services.el_workbook import ElCover, ElEmployee, ElWorkbook
from app.services.el_workbook.models import ElDependant

# Family tier -> (spouse covered, child covered) it states.
TIER_MEMBERS: dict[str, tuple[bool, bool]] = {
    "EO": (False, False), "ES": (True, False), "EC": (False, True), "EF": (True, True),
}


def _has_own_cover(dep: ElDependant, block: int) -> bool:
    return any(c.block == block for c in dep.covers)


def tier_only_blocks(workbook: ElWorkbook) -> frozenset[int]:
    """Product blocks with a family-tier column whose dependants mostly have no
    cover row of their own: the tier is how that listing states their cover.

    Decided by majority, so one leftover category cell on a dependant row does
    not turn a whole tier-stated block into a row-stated one.
    """
    out: set[int] = set()
    for index, block in enumerate(workbook.layout.blocks):
        if block.kind != "product" or block.column("family_group") is None:
            continue
        dependants = [
            dep for emp in workbook.employees
            if any(c.block == index for c in emp.covers)
            for dep in emp.dependants
        ]
        with_rows = sum(1 for dep in dependants if _has_own_cover(dep, index))
        if dependants and with_rows * 2 < len(dependants):
            out.add(index)
    return frozenset(out)


def uses_tier(emp: ElEmployee, block: int, tier_only: frozenset[int]) -> bool:
    """Whether the employee's tier states their dependants' cover under the
    block: a tier-only block, and none of their dependant rows records cover
    there (a row that does is read as stated)."""
    return block in tier_only and not any(_has_own_cover(d, block) for d in emp.dependants)


def tier_includes(tier: object, relationship: object) -> bool | None:
    """Whether a tier covers a spouse or child; None when either is unknown."""
    members = TIER_MEMBERS.get(str(tier or ""))
    if members is None or relationship not in ("spouse", "child"):
        return None
    return members[0] if relationship == "spouse" else members[1]


def employee_tier(emp: ElEmployee, block: int) -> str | None:
    cover: ElCover | None = next((c for c in emp.covers if c.block == block), None)
    tier = cover.values.get("family_group") if cover else None
    return tier if tier in TIER_MEMBERS else None


def tier_covered(emp: ElEmployee, block: int) -> list[ElDependant]:
    """The employee's listed dependants the tier under ``block`` covers."""
    tier = employee_tier(emp, block)
    return [
        dep for dep in emp.dependants
        if tier_includes(tier, dep.fields.get("relationship"))
    ]
