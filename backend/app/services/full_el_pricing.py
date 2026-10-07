"""Per-life coverage and annual pricing for Full EL; never allocate group totals."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.models import Category, Dependant, Employee
from app.models.underwriting_case import DECISION_LABELS, normalize_uw_status
from app.services.el_report_rules import mapping, positive_number, validate_report_rules
from app.services.flex_membership import classify_relationship
from app.services.full_el_data import FullELData, ProductProfile, matched_category
from app.services.insurer_listings import EmployeeCoverage, _dependant_amount
from app.services.member_premium import family_tier, member_premium
from app.services.plan_hydration import member_financials
from app.services.policy_numbers import member_entity, resolve_policy_number
from app.services.roster_attributes import REL_KEYS, anb_from_attrs, first_value, resolved_last_day
from app.services.roster_attributes import roster_date as as_date
from app.services.underwriting import report_uw_amounts


@dataclass
class MemberLine:
    status: str = "No resolved cover"
    category: str = ""
    plan: str = ""
    family: str = ""
    age: int | None = None
    start: date | None = None
    end: date | None = None
    eligible: float | None = None
    guaranteed: float | None = None
    pending: float | None = None
    accepted: float | None = None
    decision: str = ""
    decided_on: date | None = None
    requirements: str = ""
    uw_remarks: str = ""
    policy_number: str = ""
    rate_basis: str = ""
    rate: float | None = None
    net: float | None = None
    gst: float | None = None
    gross: float | None = None
    pricing: str = ""
    premium_unit: bool = False
    included: bool = False
    report_details: dict[str, Any] = field(default_factory=dict)


def _date(value: Any) -> date | None:
    parsed = as_date(value)
    return parsed if isinstance(parsed, date) else None


def _window(
    data: FullELData, p: ProductProfile, member: Any, sponsor: Employee
) -> tuple[date, date]:
    term = p.term
    start = max(term.coverage_start, data.year.start_date)
    end = min(term.coverage_end, data.year.end_date)
    for person in (member,) if member.id == sponsor.id else (member, sponsor):
        effective = _date((person.attribute_values or {}).get("effective_date"))
        if effective:
            start = max(start, effective)
        last = resolved_last_day(person)
        if person.status == "terminated" and isinstance(last, date):
            end = min(end, last)
    return start, end


def _underwriting(
    data: FullELData,
    p: ProductProfile,
    member: Any,
    line: MemberLine,
) -> None:
    case = data.cases.get((member.id, p.block.product.id))
    if case:
        line.report_details = mapping(case.report_details)
        line.decision = DECISION_LABELS.get(normalize_uw_status(case.status), case.status)
        line.decided_on, line.uw_remarks = case.decided_on, case.remarks or ""
        review = data.reviews.get(case.review_id or "")
        line.requirements = review.requirements or "" if review else ""
    if line.eligible is None:
        return
    term = p.term
    if case:
        line.pending, line.accepted = report_uw_amounts(line.eligible, term.free_cover_limit, case)
        line.guaranteed = min(
            line.eligible,
            max(0, case.guaranteed_si if case.guaranteed_si is not None else case.accepted_si),
        )
    elif p.reviewed:
        # Missing limits on life cover are unknown, rather than an automatic
        # acceptance of all SI. No download creates or updates a UW case.
        anb = anb_from_attrs(member.attribute_values or {}, term.coverage_start)
        if term.nel_age_limit is not None and (anb is None or anb >= term.nel_age_limit):
            line.decision = "Age gate requires underwriting review"
        elif p.block.product.line == "life" and term.free_cover_limit is None:
            line.decision = "Underwriting limits require review"
        elif term.underwriting_required:
            line.decision = "Underwriting case required"
        else:
            line.pending, line.accepted = report_uw_amounts(
                line.eligible, term.free_cover_limit, None
            )
            line.guaranteed = line.accepted
            line.decision = "Pending underwriting" if line.pending else "Within automatic cover"


def _dependant_category(
    data: FullELData,
    p: ProductProfile,
    cov: EmployeeCoverage,
    role: str | None,
) -> Category | None:
    options = p.block.role_options.get(role or "", [])
    chosen = (cov.dependant_option_ids or {}).get(role or "")
    for option in options:
        if (cov.option_marker and option.marker == cov.option_marker) or len(options) == 1:
            return data.categories.get(option.category_id)
    return next(
        (data.categories.get(o.category_id) for o in options if o.category_id == chosen), None
    )


def _premium(
    p: ProductProfile,
    line: MemberLine,
    pa: dict[str, Any],
    attrs: dict[str, Any],
    covered: list[Dependant],
) -> None:
    fin = member_financials(pa, line.age, attrs)
    if fin is None:
        line.pricing = "No rate configured"
        return
    line.rate_basis, line.rate = fin.rate_basis or "", fin.premium_rate
    if fin.rate_basis in ("annual_flat", "earnings_based"):
        line.pricing = "Group premium only; see Basis of Cover"
        return
    if p.block.lump_sum:
        if p.rules.premium_si_basis not in ("Eligible SI", "Accepted SI"):
            line.pricing = "Premium SI basis requires review"
            return
        amount = line.eligible if p.rules.premium_si_basis == "Eligible SI" else line.accepted
        rate = positive_number(fin.premium_rate)
        if amount is None or rate is None:
            line.pricing = "Missing billing SI or rate"
            return
        if fin.rate_basis != "per_1000_si" and not fin.voluntary_rates:
            line.pricing = "Unsupported SI rate basis; review setup"
            return
        line.net = round(amount / 1000 * rate, 2)
        line.pricing = f"{p.rules.premium_si_basis} / 1000 x rate"
    else:
        roles = [
            classify_relationship(first_value(d.attribute_values or {}, REL_KEYS)) for d in covered
        ]
        if any(role not in ("spouse", "child") for role in roles):
            line.pricing = "Covered dependant relationship requires review"
            return
        premium = member_premium(fin, spouses=roles.count("spouse"), children=roles.count("child"))
        if premium is None or positive_number(premium.amount) is None:
            line.pricing = "No applicable member / family rate"
            return
        line.net, line.pricing = premium.amount, premium.note
    _tax(p, line)


def _tax(p: ProductProfile, line: MemberLine) -> None:
    if line.net is None:
        return
    term = p.term
    if term.gst_included is False:
        line.gst, line.gross = 0.0, line.net
    elif term.gst_included is True and term.gst_rate is not None:
        line.gst = round(line.net * term.gst_rate / 100, 2)
        line.gross = round(line.net + line.gst, 2)
    else:
        line.pricing += "; GST not established"


def _price_gate(data: FullELData, p: ProductProfile, cat: Category | None) -> str:
    if not p.reviewed:
        return "Product setup requires confirmation"
    if cat is None or cat.status != "confirmed" or cat.rule_status not in (None, "validated"):
        return "Category mapping requires review"
    if not p.rules.currency:
        return "Premium currency requires review"
    try:
        validate_report_rules(p.answers)
    except ValueError as exc:
        return str(exc)
    if p.answers.get("source_issues") and not p.answers.get("source_reviewed"):
        return "Source issues require review"
    return p.rate_period_error(data.year)


def _individual_price(data: FullELData, p: ProductProfile, member: Any, line: MemberLine) -> bool:
    details = line.report_details
    net = details.get("annual_premium_net")
    if net is None:
        if line.decision == "Approved Substandard Life":
            line.pricing = "Substandard cover requires an insurer-confirmed annual premium"
            return True
        return False
    case = data.cases.get((member.id, p.block.product.id))
    if not p.block.lump_sum:
        line.pricing = "Individual override requires a reviewed member / family premium basis"
    elif details.get("premium_currency") != p.rules.currency:
        line.pricing = "Individual premium currency differs from the product setup"
    elif (
        line.eligible is None
        or details.get("premium_eligible_si") != line.eligible
        or details.get("premium_accepted_si") != line.accepted
        or case is None
        or case.eligible_si != line.eligible
        or details.get("premium_status") != normalize_uw_status(case.status)
    ):
        line.pricing = "Reconfirm the premium after the SI or underwriting decision change"
    else:
        line.net = net
        line.pricing = "Insurer-confirmed individual annual premium"
        _tax(p, line)
    return True


def _covered_family(
    data: FullELData,
    p: ProductProfile,
    emp: Employee,
    cov: EmployeeCoverage,
) -> list[Dependant]:
    covered = []
    for did in cov.covered_dependant_ids:
        dep = data.dependants_by_id.get(did)
        if dep is not None:
            start, end = _window(data, p, dep, emp)
            if start <= end:
                covered.append(dep)
    return covered


def _price_member(
    data: FullELData,
    p: ProductProfile,
    emp: Employee,
    member: Any,
    cat: Category | None,
    line: MemberLine,
    pa: dict[str, Any],
    covered: list[Dependant],
) -> None:
    line.premium_unit = True
    line.pricing = _price_gate(data, p, cat)
    if not line.pricing and not p.block.lump_sum:
        substandard = any(
            (case := data.cases.get((dep.id, p.block.product.id)))
            and normalize_uw_status(case.status) == "approved_substandard"
            for dep in covered
        )
        if substandard:
            line.pricing = "Substandard dependant cover requires a reviewed family premium"
    if not line.pricing and not _individual_price(data, p, member, line):
        _premium(p, line, pa, member.attribute_values or {}, covered)
    if line.net is None:
        data.gap(
            p.block.product.code,
            "Annual premium",
            line.pricing,
            "Review the member's assigned category, rates and Full EL rules.",
            cat.source_ref or p.source if cat else p.source,
            emp.staff_id,
        )


def member_line(
    data: FullELData,
    p: ProductProfile,
    emp: Employee,
    dep: Dependant | None = None,
) -> MemberLine:
    member = dep or emp
    attrs = member.attribute_values or {}
    line = MemberLine(age=p.rules.age(attrs, p.term.coverage_start, data.year.start_date))
    cov = data.coverage.get(emp.id, {}).get(p.block.product.id)
    if cov is None or (dep is not None and dep.id not in cov.covered_dependant_ids):
        override = data.overrides.get((emp.id, p.block.product.id))
        if override and override.declined:
            line.status = "Declined cover"
        return line
    line.start, line.end = _window(data, p, member, emp)
    if line.start > line.end:
        line.status = "Outside product / benefit period"
        return line
    cat = matched_category(data, emp, p)
    line.included = True
    line.status = "Resolved cover" if p.reviewed else "Provisional cover - review setup"
    line.category = cat.display_name if cat else ""
    covered = _covered_family(data, p, emp, cov)
    roles = [
        classify_relationship(first_value(d.attribute_values or {}, REL_KEYS)) for d in covered
    ]
    line.plan = cov.plan_label or cov.plan_code or ""
    line.family = (
        family_tier(roles.count("spouse"), roles.count("child"))
        if all(role in ("spouse", "child") for role in roles)
        else "Review relationship"
    )
    line.policy_number = (
        resolve_policy_number(p.block.term, member_entity(emp.attribute_values)).number or ""
    )
    if dep is not None and p.block.lump_sum:
        role = classify_relationship(first_value(attrs, REL_KEYS))
        line.eligible = _dependant_amount(p.block, cov, role)
        cat = _dependant_category(data, p, cov, role)
        line.category = cat.display_name if cat else ""
    elif p.block.lump_sum:
        line.eligible = cov.eligible
    pa = mapping(cat.plan_assignments) if cat else {}
    if p.reviewed and p.rules.max_sum_insured is not None and line.eligible is not None:
        line.eligible = min(line.eligible, p.rules.max_sum_insured)
        pa = {**pa, "max_sum_insured": p.rules.max_sum_insured}
    has_source_cap = (
        "maximum sum" in p.source_text or "maximum limit per insured person" in p.source_text
    )
    if p.block.lump_sum and has_source_cap and p.rules.max_sum_insured is None:
        line.eligible = None
        data.gap(
            p.block.product.code,
            "Maximum SI",
            "Source maximum SI has not been reviewed",
            "Record the maximum sum insured in Header & Policy.",
            p.source,
            emp.staff_id,
        )
    _underwriting(data, p, member, line)
    if p.rules.age_basis and p.rules.age_reference and line.age is None:
        data.gap(
            p.block.product.code,
            "Age",
            "Age cannot be calculated from the roster",
            "Check date of birth and the placement's age reference date.",
            p.source,
            emp.staff_id,
        )
    if p.block.lump_sum and line.accepted is None:
        data.gap(
            p.block.product.code,
            "Underwriting",
            line.decision or "Accepted SI is not established",
            "Review cover limits and the member's underwriting decision.",
            p.source,
            emp.staff_id,
        )
    if dep is not None and not p.block.lump_sum:
        line.pricing = "Included in employee / family premium; not counted twice"
        return line
    _price_member(data, p, emp, member, cat, line, pa, covered if dep is None else [])
    return line
