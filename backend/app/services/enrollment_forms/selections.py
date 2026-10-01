"""What the member is signing for, product by product — resolved ONCE and then
read by every check and by the snapshot, so the form can't validate one thing
and print another.

**Who is covered** follows the coverage rule (``benefit_statement`` /
``coverage_resolver``): an explicit ``covered_dependant_ids`` list is
authoritative; ``None`` means "no change", so the member keeps what they hold
(the window's baseline list), and with no baseline list compulsory family cover
covers every eligible active dependant while voluntary cover covers nobody.
Reading ``None`` as "nobody" printed kept family members as WITHDRAWN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status

from app.models.enrollment import EnrollmentElection
from app.schemas.enrollment import CohortTierOut, ProductTierSetOut
from app.schemas.enrollment_forms import (
    FormDependantOut,
    MemberFormContextOut,
    PendingDependantRequest,
    PlanFactOut,
    ProductContributionOut,
)
from app.services.enrollment_forms.context import current_tier_label
from app.services.enrollment_forms.pricing import tier_dependant_participation


def unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)


@dataclass
class Selection:
    ts: ProductTierSetOut
    election: EnrollmentElection | None
    tier: CohortTierOut | None
    declined: bool
    family_mode: str | None  # compulsory | voluntary | None (no family cover)
    covered_ids: list[str] = field(default_factory=list)
    withdrawn_ids: list[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        return self.ts.product_code

    @property
    def name(self) -> str:
        return self.ts.product_name or self.ts.product_code


def _find_tier(ts: ProductTierSetOut, el: EnrollmentElection | None) -> CohortTierOut | None:
    """The elected tier, or — with no election for the product — the held one."""
    if el is None:
        return next((t for t in ts.tiers if t.is_current), None) or next(
            (t for t in ts.tiers if t.is_baseline), None
        )
    if el.elected_plan_code is None:
        return None
    if el.tier_category_id:
        for t in ts.tiers:
            if t.tier_category_id == el.tier_category_id and t.plan_code == el.elected_plan_code:
                return t
    matches = [t for t in ts.tiers if t.plan_code == el.elected_plan_code]
    preferred = next((t for t in matches if t.is_current or t.is_baseline), None)
    return preferred or (matches[0] if matches else None)


def _auto_covered(code: str, deps: list[FormDependantOut]) -> list[str]:
    """Compulsory family cover: every active family member inside THIS
    product's age window."""
    return [
        d.id for d in deps
        if d.status == "active" and d.role is not None and code not in d.ineligible_products
    ]


def resolve_selections(
    products: list[ProductTierSetOut],
    elections: dict[str, EnrollmentElection],
    ctx: MemberFormContextOut,
    baseline: dict[str, Any],
) -> list[Selection]:
    out: list[Selection] = []
    for ts in products:
        el = elections.get(ts.product_code)
        declined = el is not None and el.elected_plan_code is None
        tier = _find_tier(ts, el)
        mode = None if declined else tier_dependant_participation(ts, tier)
        held = (baseline.get(ts.product_code) or {}).get("covered_dependant_ids")
        sel = Selection(ts=ts, election=el, tier=tier, declined=declined, family_mode=mode)
        if not declined and mode is not None:
            if el is not None and el.covered_dependant_ids is not None:
                sel.covered_ids = list(el.covered_dependant_ids)
            elif held is not None:
                sel.covered_ids = list(held)
            elif mode == "compulsory":
                sel.covered_ids = _auto_covered(ts.product_code, ctx.dependants)
        if held is not None:
            sel.withdrawn_ids = [i for i in held if i not in sel.covered_ids]
        out.append(sel)
    return out


# -- Checks (each mirrored client-side in formMath.ts) ------------------------


def names_family(selections: list[Selection], pending: list[PendingDependantRequest]) -> bool:
    """Does the member put a family member on VOLUNTARY cover (or ask to)? Only
    then do the family declarations apply — compulsory family cover is a fact of
    the plan, not something the member is applying for."""
    if any(req.product_codes for req in pending):
        return True
    return any(s.family_mode == "voluntary" and s.covered_ids for s in selections)


def check_eligibility(selections: list[Selection], ctx: MemberFormContextOut) -> None:
    deps = {d.id: d for d in ctx.dependants}
    problems: list[str] = []
    for sel in selections:
        if sel.family_mode != "voluntary":
            continue  # compulsory cover drops an out-of-window dependant itself
        for dep_id in sel.covered_ids:
            dep = deps.get(dep_id)
            if dep is not None and sel.code in dep.ineligible_products:
                problems.append(f"{dep.name or 'A family member'} on {sel.name}")
    if problems:
        raise unprocessable(
            "These family members are outside the plan's age limit — remove them: "
            + "; ".join(problems)
        )


def check_rules(selections: list[Selection], ctx: MemberFormContextOut) -> None:
    by_code = {s.code: s for s in selections}
    names = {d.id: d.name or "A family member" for d in ctx.dependants}
    for rule in ctx.rules:
        a, b = by_code.get(rule.product_code), by_code.get(rule.requires_product_code)
        if a is None or b is None or a.declined:
            continue
        if b.declined:
            raise unprocessable(f"{a.name} can only be taken together with {b.name}.")
        if a.family_mode is None or b.family_mode is None:
            continue
        missing = [i for i in a.covered_ids if i not in b.covered_ids]
        if missing:
            who = ", ".join(sorted(names.get(i, "A family member") for i in missing))
            raise unprocessable(
                f"{who} must also be covered on {b.name} to be covered on {a.name}."
            )


def check_pending(
    requests: list[PendingDependantRequest],
    selections: list[Selection],
    ctx: MemberFormContextOut,
    allow_dependant_changes: bool,
) -> list[dict[str, Any]]:
    """Requests to put a family member awaiting verification on a plan. Only for
    plans the member keeps that take VOLUNTARY family cover, inside that plan's
    age window, and only when the period allows family changes at all."""
    wanted = [r for r in requests if r.product_codes]
    if wanted and not allow_dependant_changes:
        raise unprocessable("This enrolment period does not allow changes to family cover.")
    deps = {d.id: d for d in ctx.dependants}
    allowed = {s.code: s for s in selections if s.family_mode == "voluntary"}
    out: list[dict[str, Any]] = []
    for req in wanted:
        dep = deps.get(req.dependant_id)
        if dep is None or dep.status != "pending":
            raise unprocessable("A requested family member is not awaiting verification.")
        for code in req.product_codes:
            if code not in allowed:
                raise unprocessable(
                    f"{dep.name or 'A family member'} can't be requested on {code}: you "
                    "aren't keeping that plan, or it doesn't take family members."
                )
            if code in dep.ineligible_products or not dep.eligible:
                raise unprocessable(
                    f"{dep.name or 'A family member'} is outside the age limit for {code}."
                )
        out.append({
            "dependant_id": dep.id,
            "name": dep.name,
            "product_codes": sorted(set(req.product_codes)),
        })
    return out


# -- Snapshot rows -----------------------------------------------------------


def _you_pay(
    contrib: ProductContributionOut | None, tier_key: str | None, roles: list[str | None]
) -> float | None:
    if contrib is None or tier_key is None:
        return None
    tier = next((t for t in contrib.tiers if t.tier_key == tier_key), None)
    if tier is None:
        return None
    spouses = roles.count("spouse")
    children = roles.count("child")
    parts: list[float | None] = [tier.employee]
    if spouses or children:
        if tier.mode == "tiered":
            role = "both" if spouses and children else "spouse" if spouses else "child"
            parts.append(tier.family.get(role))
        else:
            per_head = tier.per_dependant
            parts.append(per_head * (spouses + children) if per_head is not None else None)
    known = [p for p in parts if p is not None]
    return round(sum(known), 2) if known else None


def _price_tag(sel: Selection) -> float | None:
    """Flex dollars this plan draws: the election's snapshot (employee plan +
    covered family, priced by the same resolver the deck shows), else — with no
    election for the product — the held tier's own tag."""
    if sel.declined:
        return 0.0
    if sel.election is not None:
        return sel.election.flex_price_tag
    return sel.tier.price_tag if sel.tier else None


def selection_rows(selections: list[Selection], ctx: MemberFormContextOut) -> list[dict[str, Any]]:
    deps = {d.id: d for d in ctx.dependants}
    contribs = {c.product_code: c for c in ctx.contributions}
    facts: dict[tuple[str, str], PlanFactOut] = {
        (f.product_code, f.tier_key): f for f in ctx.plans
    }
    compulsory = {c.product_code for c in ctx.compulsory}
    rows: list[dict[str, Any]] = []
    for sel in selections:
        key = sel.tier.key if sel.tier else None
        fact = facts.get((sel.code, key)) if key else None
        contrib = contribs.get(sel.code)
        premium = (
            next((t for t in contrib.tiers if t.tier_key == key), None) if contrib and key else None
        )
        rows.append({
            "product_code": sel.code,
            "product_name": sel.ts.product_name,
            "insurer": fact.insurer if fact else None,
            "compulsory": sel.code in compulsory,
            "action": sel.election.action if sel.election else "keep",
            "previous_plan": current_tier_label(sel.ts),
            "elected_plan": None if sel.declined else (sel.tier.label if sel.tier else None),
            "highlight": fact.highlight if fact else None,
            "sum_insured": fact.sum_insured if fact else None,
            "declined": sel.declined,
            "family_mode": sel.family_mode,
            "covered": [deps[i].name or "-" for i in sel.covered_ids if i in deps],
            "covered_ids": sel.covered_ids,
            "withdrawn": [deps[i].name or "-" for i in sel.withdrawn_ids if i in deps],
            "premium": premium.premium if premium else None,
            "premium_per_dependant": premium.premium_per_dependant if premium else None,
            "employee_pct": contrib.employee_pct if contrib else None,
            "dependant_pct": contrib.dependant_pct if contrib else None,
            "price_tag": _price_tag(sel),
            "you_pay": _you_pay(
                contrib, key, [deps[i].role for i in sel.covered_ids if i in deps]
            ),
        })
    return rows
