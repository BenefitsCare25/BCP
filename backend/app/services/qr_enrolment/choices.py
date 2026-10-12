"""Check a QR submission against the form's own figures and price each choice.

The browser computes the same figures for display; the server recomputes
every one, so what the PDF prints is what the configuration says — never what
the page claimed. Problems are collected and returned together, phrased for
the employee, so one round trip shows everything that needs fixing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.schemas.enrollment import ProductTierSetOut
from app.schemas.enrollment_forms import ContributionTierOut, FormContribution, PlanFactOut
from app.schemas.qr_enrolment import (
    PublicDependantIn,
    PublicProductChoiceIn,
    PublicSubmissionIn,
)
from app.services.enrollment_forms.pricing import contribution_tier, own_premium
from app.services.qr_enrolment.catalog import MAX_CHILDREN, Catalog
from app.services.roster_attributes import (
    age_next_birthday_as_of,
    is_valid_sg_nric,
    looks_like_sg_nric,
)

_ROLE = {"spouse": "Spouse", "child": "Child"}
_ACTION = {"add": "Add to cover", "withdraw": "Withdraw from cover", "update": "Update details"}
_SEX = {"M": "Male", "F": "Female"}


@dataclass
class Checked:
    selections: list[dict[str, Any]] = field(default_factory=list)
    dependants: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    category_id: str | None = None


def _composition(spouses: int, children: int) -> str:
    if spouses and children:
        return "EF"
    if spouses:
        return "ES"
    return "EC" if children else "EO"


def _role(spouses: int, children: int) -> str | None:
    if spouses and children:
        return "both"
    if spouses:
        return "spouse"
    return "child" if children else None


def price(
    tier: ContributionTierOut | None, spouses: int, children: int, own: bool
) -> tuple[float | None, float | None]:
    """(full annual premium, employee's share) for one composition, or None
    where the figure is not configured or depends on the person."""
    if tier is None:
        return None, None
    n = spouses + children
    if tier.mode == "tiered":
        full = tier.premium.get(_composition(spouses, children))
        role = _role(spouses, children)
        family = tier.family.get(role) if role else 0.0
        if not own and n == 0:
            # Company-paid own cover with no family on it: nothing to pay.
            return full, 0.0
        parts = [tier.employee if own else 0.0, family]
        share = None if any(p is None for p in parts) else round(sum(p or 0.0 for p in parts), 2)
        return full, share
    base = tier.premium.get("EO")
    per = tier.premium_per_dependant or 0.0
    full = None if base is None else round(base + per * n, 2)
    own_share = tier.employee if own else 0.0
    dep_share = (tier.per_dependant or 0.0) * n if n else 0.0
    share = None if own_share is None else round(own_share + dep_share, 2)
    return full, share


def _contribution(
    ts: ProductTierSetOut, key: str, share: FormContribution | None
) -> ContributionTierOut | None:
    if share is None:
        return None
    tier = next((t for t in ts.tiers if t.key == key), None)
    base = next((t for t in ts.tiers if t.is_baseline), None)
    if tier is None:
        return None
    base_own = own_premium(base.financials) if base else None
    return contribution_tier(key, tier.financials, share, base_own)


def _check_dependants(
    body: PublicSubmissionIn, allow_changes: bool, out: Checked
) -> dict[str, PublicDependantIn]:
    deps = {d.key: d for d in body.dependants}
    spouses = [d for d in body.dependants if d.relationship == "spouse"]
    children = [d for d in body.dependants if d.relationship == "child"]
    if len(spouses) > 1:
        out.problems.append("Name only one spouse.")
    if len(children) > MAX_CHILDREN:
        out.problems.append(f"Name at most {MAX_CHILDREN} children on one form.")
    for d in body.dependants:
        if not allow_changes and d.action != "update":
            out.problems.append(
                "Family members cannot be added or withdrawn in this enrolment period."
            )
            break
    for d in body.dependants:
        if looks_like_sg_nric(d.id_no) and not is_valid_sg_nric(d.id_no):
            out.problems.append(
                f"Check the NRIC / birth certificate / FIN number for {d.name}."
            )
        if d.relationship == "child" and d.action == "add" and not d.child_confirmed:
            out.problems.append(
                f"Confirm that {d.name} is unmarried, not working full time and not "
                "serving National Service."
            )
    return deps


def _ages_ok(
    covered: list[PublicDependantIn], limits: dict[str, dict[str, int]], ref: date,
    product: str, out: Checked,
) -> None:
    for d in covered:
        window = limits.get(d.relationship) or {}
        anb = age_next_birthday_as_of(d.dob, ref)
        lo, hi = window.get("min"), window.get("max")
        if (lo is not None and anb < lo) or (hi is not None and anb > hi):
            out.problems.append(f"{d.name} is outside the age limit for {product}.")


def _row(
    catalog: Catalog,
    choice: PublicProductChoiceIn,
    deps: dict[str, PublicDependantIn],
    facts: dict[str, PlanFactOut],
    ref: date,
    out: Checked,
) -> dict[str, Any] | None:
    product = next((p for p in catalog.products if p.product_code == choice.product_code), None)
    if product is None:
        out.problems.append("One of the benefits chosen is not offered on this form.")
        return None
    name = product.product_name or product.product_code
    cohort = catalog.cohort(choice.product_code, choice.category_id)
    if cohort is None:
        out.problems.append(f"Choose your category for {name}.")
        return None
    ts = cohort.tier_set
    row: dict[str, Any] = {
        "product_code": product.product_code,
        "product_name": name,
        "insurer": product.insurer,
        "category": cohort.label,
        "category_id": cohort.category_id,
        "compulsory": ts.employee_participation == "compulsory",
        "action": choice.action,
        "previous_plan": None,
        "declined": False,
        "covered": [],
        "withdrawn": [],
    }
    if choice.action == "no_change":
        row["elected_plan"] = "No change"
        return row
    if choice.action == "withdraw":
        return _withdraw_row(row, choice, deps, ts, name, out)
    tier = next((t for t in ts.tiers if t.key == choice.tier_key), None)
    if tier is None:
        out.problems.append(f"Choose a plan for {name}.")
        return None
    if not ts.allow_plan_change and not tier.is_baseline:
        out.problems.append(f"{name} has no choice of plan in this period.")
        return None
    covered = [deps[k] for k in choice.covered if k in deps]
    if len(covered) != len(choice.covered) or any(d.action == "withdraw" for d in covered):
        out.problems.append(f"Tick only family members you are enrolling for {name}.")
        return None
    if covered and tier.dependant_participation != "voluntary":
        out.problems.append(f"{name} does not offer family cover on this plan.")
        return None
    _ages_ok(covered, product.age_limits, ref, name, out)
    spouses = sum(d.relationship == "spouse" for d in covered)
    children = len(covered) - spouses
    share = catalog.settings.contributions.get(product.product_code)
    contribution = _contribution(ts, tier.key, share)
    own = ts.employee_participation == "voluntary" or not tier.is_baseline
    full, you_pay = price(contribution, spouses, children, own)
    fact = facts.get(tier.key)
    row.update({
        "elected_plan": tier.label,
        "tier_key": tier.key,
        "covered": [d.name for d in covered],
        "covered_keys": [d.key for d in covered],
        "composition": _composition(spouses, children),
        "premium": contribution.premium if contribution else {},
        "premium_per_dependant": contribution.premium_per_dependant if contribution else None,
        "full_premium": full,
        "you_pay": you_pay,
        "employee_pct": share.employee_pct if share else None,
        "dependant_pct": share.dependant_pct if share else None,
        "upgrade_pct": share.upgrade_pct if share else None,
        "highlight": fact.highlight if fact else None,
        "sum_insured": fact.sum_insured if fact else None,
        "basis": fact.basis if fact else None,
        "max_sum_insured": fact.max_sum_insured if fact else None,
    })
    return row


def _withdraw_row(
    row: dict[str, Any],
    choice: PublicProductChoiceIn,
    deps: dict[str, PublicDependantIn],
    ts: ProductTierSetOut,
    name: str,
    out: Checked,
) -> dict[str, Any] | None:
    gone = [deps[k] for k in choice.covered if k in deps]
    if len(gone) != len(choice.covered):
        out.problems.append(f"Tick the family members to withdraw from {name}.")
        return None
    if choice.cover_self and ts.employee_participation != "voluntary":
        out.problems.append(f"Your own {name} cover is compulsory and cannot be withdrawn.")
        return None
    if not gone and not choice.cover_self:
        out.problems.append(f"Choose who to withdraw from {name}.")
        return None
    row.update({
        "elected_plan": "Withdraw" if choice.cover_self else "Withdraw family cover",
        "declined": choice.cover_self,
        "withdrawn": [d.name for d in gone],
        "withdrawn_keys": [d.key for d in gone],
    })
    return row


def _rules(catalog: Catalog, rows: list[dict[str, Any]], out: Checked) -> None:
    by_code = {r["product_code"]: r for r in rows}
    for rule in catalog.settings.rules:
        mine = by_code.get(rule.product_code)
        needed = by_code.get(rule.requires_product_code)
        if mine is None or mine["action"] != "enrol" or needed is None:
            continue
        a = catalog.names.get(rule.product_code, rule.product_code)
        b = catalog.names.get(rule.requires_product_code, rule.requires_product_code)
        if needed["action"] == "withdraw" and needed.get("declined"):
            out.problems.append(f"{a} must be taken together with {b}.")
            continue
        if needed["action"] == "enrol":
            extra = set(mine.get("covered_keys", [])) - set(needed.get("covered_keys", []))
            if extra:
                out.problems.append(
                    f"Family members covered under {a} must also be covered under {b}."
                )


def _dependant_rows(
    body: PublicSubmissionIn, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    on: dict[str, list[str]] = {}
    for r in rows:
        for key in r.get("covered_keys", []):
            on.setdefault(key, []).append(r["product_name"])
    return [
        {
            "id": d.key,
            "relationship": _ROLE[d.relationship],
            "name": d.name,
            "gender": _SEX[d.gender],
            "id_no": d.id_no,
            "occupation": d.occupation,
            "dob": d.dob.isoformat(),
            "nationality": d.nationality,
            "residence": d.residence,
            "action": d.action,
            "action_label": _ACTION[d.action],
            "status": "pending" if d.action == "add" else "on_record",
            "covered_on": on.get(d.key, []),
        }
        for d in body.dependants
    ]


def check_submission(
    catalog: Catalog,
    body: PublicSubmissionIn,
    facts: list[PlanFactOut],
    ref: date,
    allow_dependant_changes: bool,
) -> Checked:
    out = Checked()
    by_key = {f.tier_key: f for f in facts}
    if not is_valid_sg_nric(body.particulars.id_no):
        out.problems.append("Enter a valid NRIC or FIN number.")
    if " ".join(body.signature_name.split()).casefold() != body.particulars.name.casefold():
        out.problems.append("Type your full name exactly as entered in Your details to sign.")
    deps = _check_dependants(body, allow_dependant_changes, out)
    chosen = {c.product_code for c in body.products}
    rows = [
        r for c in body.products
        if (r := _row(catalog, c, deps, by_key, ref, out)) is not None
    ]
    for product in catalog.products:
        if product.product_code not in chosen:
            label = product.product_name or product.product_code
            out.problems.append(f"Choose an option for {label}.")
    _rules(catalog, rows, out)
    named = bool(body.dependants)
    required = {c.id for c in catalog.clauses() if c.applies_to == "all" or named}
    if required - set(body.accepted_clause_ids):
        out.problems.append("Tick every declaration before signing.")
    out.selections = rows
    out.dependants = _dependant_rows(body, rows)
    out.category_id = next((r["category_id"] for r in rows if r.get("category_id")), None)
    return out
