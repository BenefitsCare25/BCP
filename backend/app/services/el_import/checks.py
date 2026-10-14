"""Cover consistency checks on an uploaded Employee Listing.

Findings are reported for the broker's review; none blocks the import. They
name rows, never personal data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.schemas.employee_listing import ListingCheck
from app.services.coverage_limits import ProductLimits
from app.services.el_import.family_tiers import (
    TIER_MEMBERS,
    tier_covered,
    tier_only_blocks,
    uses_tier,
)
from app.services.el_import.mapping import LabelSuggestion, normalize_label
from app.services.el_import.records import dependant_cover_start
from app.services.el_workbook import ElCover, ElEmployee, ElWorkbook
from app.services.el_workbook.models import ElDependant
from app.services.el_workbook.values import text


@dataclass(frozen=True)
class CheckContext:
    """What the checks compare the listing with: the policy year's start and
    each product's slip limits, keyed by product code."""

    period_start: date | None = None
    limits: dict[str, ProductLimits] = field(default_factory=dict)


def _check(code: str, message: str, rows: list[int]) -> ListingCheck | None:
    if not rows:
        return None
    rows = sorted(set(rows))
    return ListingCheck(code=code, message=message, count=len(rows), rows=rows[:50])


def _product(decisions: dict[tuple[int, str], LabelSuggestion], cover: ElCover) -> str | None:
    decision = decisions.get((cover.block, normalize_label(text(cover.values.get("category")))))
    return decision.choice.product_code if decision and decision.choice else None


def _covered_dependants(
    emp: ElEmployee, block: int, tier_only: frozenset[int]
) -> list[ElDependant]:
    if uses_tier(emp, block, tier_only):
        return tier_covered(emp, block)
    return [d for d in emp.dependants if any(c.block == block for c in d.covers)]


def _tier_disagrees(
    emp: ElEmployee, tier: object, covered: list[ElDependant], by_tier: bool
) -> bool:
    """Whether the tier contradicts the dependants listed.

    Where rows state cover, the tier must match the dependants covered under
    the block exactly. Where the tier states it, only a tier naming a spouse
    or child the file does not list is wrong: a spouse listed just for dental
    beside an EO hospital tier is consistent.
    """
    if tier not in TIER_MEMBERS:
        return False
    spouse, child = TIER_MEMBERS[str(tier)]
    people = emp.dependants if by_tier else covered
    relations = {d.fields.get("relationship") for d in people}
    if by_tier:
        return (spouse and "spouse" not in relations) or (child and "child" not in relations)
    return (spouse, child) != ("spouse" in relations, "child" in relations)


def _over_limit(limits: ProductLimits, born: object, limit: int | None, ref: date) -> bool:
    if not limit or not isinstance(born, date):
        return False
    age = limits.age(born, ref)
    return age is not None and age > limit


class _Findings:
    def __init__(self) -> None:
        self.no_cover: list[int] = []
        self.tier_mismatch: list[int] = []
        self.no_admin: list[int] = []
        self.orphan_cover: list[int] = []
        self.movement: list[int] = []
        self.mid_year: list[int] = []
        self.over_age: list[int] = []
        self.not_eligible: list[int] = []
        self.over_age_products: set[str] = set()
        self.not_eligible_products: set[str] = set()


def _tier_and_eligibility(
    emp: ElEmployee, decisions: dict[tuple[int, str], LabelSuggestion],
    tiered: set[int], tier_only: frozenset[int], ctx: CheckContext, out: _Findings,
) -> None:
    own = {c.block: c for c in emp.covers}
    for block, cover in own.items():
        covered = _covered_dependants(emp, block, tier_only)
        if block in tiered and _tier_disagrees(
            emp, cover.values.get("family_group"), covered,
            uses_tier(emp, block, tier_only),
        ):
            out.tier_mismatch.append(emp.row)
        code = _product(decisions, cover)
        limits = ctx.limits.get(code or "")
        if limits is None:
            continue
        ref = limits.cover_start or ctx.period_start
        if ref is not None and _over_limit(
            limits, emp.fields.get("date_of_birth"), limits.employee_age_limit, ref
        ):
            out.over_age.append(emp.row)
            out.over_age_products.add(limits.code)
        for dep in covered:
            relation = dep.fields.get("relationship")
            if limits.setup_has_member_cover and relation in ("spouse", "child") and (
                relation.capitalize() not in limits.roles
            ):
                out.not_eligible.append(dep.row)
                out.not_eligible_products.add(limits.code)
            limit = {"spouse": limits.spouse_age_limit,
                     "child": limits.child_age_limit}.get(str(relation))
            if ref is not None and _over_limit(limits, dep.fields.get("date_of_birth"), limit, ref):
                out.over_age.append(dep.row)
                out.over_age_products.add(limits.code)


def cover_checks(
    workbook: ElWorkbook,
    decisions: list[LabelSuggestion],
    ctx: CheckContext | None = None,
) -> list[ListingCheck]:
    ctx = ctx or CheckContext()
    layout = workbook.layout
    tiered = {
        i for i, b in enumerate(layout.blocks)
        if b.kind == "product" and b.column("family_group") is not None
    }
    tier_only = tier_only_blocks(workbook)
    with_admin = {
        i for i, b in enumerate(layout.blocks)
        if b.kind == "product" and b.column("admin_type") is not None
    }
    by_key = {(d.block, d.key): d for d in decisions}
    out = _Findings()
    for emp in workbook.employees:
        if not emp.covers:
            out.no_cover.append(emp.row)
        if text(emp.fields.get("movement")):
            out.movement.append(emp.row)
        own = {c.block for c in emp.covers}
        for cover in emp.covers:
            if cover.block in with_admin and not cover.values.get("admin_type"):
                out.no_admin.append(emp.row)
        _tier_and_eligibility(emp, by_key, tiered, tier_only, ctx, out)
        for dep in emp.dependants:
            if dependant_cover_start(dep, ctx.period_start) is not None:
                out.mid_year.append(dep.row)
            for cover in dep.covers:
                if cover.block not in own:
                    out.orphan_cover.append(dep.row)
                if cover.block in with_admin and not cover.values.get("admin_type"):
                    out.no_admin.append(dep.row)
    over_age = ", ".join(sorted(out.over_age_products))
    not_eligible = ", ".join(sorted(out.not_eligible_products))
    findings = [
        _check("over_age_limit", f"Above the slip's age limit for {over_age}. Confirm the "
               "insurer accepts them before applying.", out.over_age),
        _check("dependant_not_eligible", f"Dependants listed under {not_eligible}, whose "
               "member cover eligibility does not include their relationship. They are not "
               "covered there; update the setup's member cover or the listing.",
               out.not_eligible),
        _check("family_tier_mismatch", "Family tier (EO/ES/EC/EF) does not match the "
               "dependants listed for the employee.", out.tier_mismatch),
        _check("dependant_without_employee_cover", "Dependants covered under a product "
               "their employee is not covered under.", out.orphan_cover),
        _check("no_cover", "Employees listed with no cover under any product.", out.no_cover),
        _check("mid_year_dependant", "Dependants added during the year (newborns or a "
               "WEF date in the remarks). Their cover starts on that date.", out.mid_year),
        _check("missing_admin_type", "Covered rows without a type of administration "
               "(headcount / named).", out.no_admin),
        _check("movement_note", "Rows carrying a movement note; review whether the "
               "change applies from a later date.", out.movement),
    ]
    return [f for f in findings if f is not None]
