"""Cell values and live formulas for one person's row of the Employee Listing.

Formulas follow the client's own workbook: ages from the date of birth against
the reference date in row 1 (a product block uses its reviewed age basis and
reference date where they differ), sums insured from salary multiples,
premiums from the rate and the eligible (or reviewed accepted) sum insured,
GST as a gross-up. Values the slip or the listing cannot establish stay blank
(and are reported as gaps) rather than being guessed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.models import Dependant, Employee, UnderwritingCase
from app.models.employee_listing import ListingAssignment
from app.services.el_workbook import ElBlock
from app.services.full_el.context import CategoryInfo, ElContext, ProductInfo
from app.services.insurer_reports import safe_cell
from app.services.roster_attributes import mask_nric, roster_date
from app.services.underwriting import report_uw_amounts

DATE_ROLES = {"date_of_birth", "date_of_hire", "last_day_of_service", "mu_letter_member",
              "mu_letter_insurer", "acceptance_date", "retrenchment_start", "retrenchment_end",
              "reemployment_start", "reemployment_end"}
_EMPLOYEE_ATTRS = {
    "national_id": "id_no", "gender": "gender", "department": "department",
    "designation": "designation", "grade": "grade", "citizenship": "citizenship",
    "nationality": "nationality", "date_of_birth": "date_of_birth", "salary": "salary",
    "date_of_hire": "date_of_hire", "bank_code": "bank_code", "bank_branch": "branch_code",
    "bank_account": "bank_account_no", "email": "email", "mobile": "mobile",
    "entity": "entity", "country_of_work": "country_of_work",
    "marital_status": "marital_status", "cost_centre": "cost_centre", "currency": "currency",
    "last_day_of_service": "last_day_of_service", "remarks": "remarks",
    "retrenchment_start": "retrenchment_start", "retrenchment_end": "retrenchment_end",
    "reemployment_start": "reemployment_start", "reemployment_end": "reemployment_end",
}
_DEPENDANT_ATTRS = {
    "name": "dependant_name", "relationship": "relationship", "gender": "gender",
    "national_id": "dependant_id_no", "nationality": "nationality",
    "residence": "country_of_residence", "date_of_birth": "date_of_birth",
}
_RECORDED = ("present_si", "mu_status", "mu_decision", "mu_letter_member",
             "mu_letter_insurer", "last_accepted_si", "loading_rate", "acceptance_date")


@dataclass(frozen=True)
class Refs:
    """Column letters a row's formulas point at."""

    dob: str | None
    dep_dob: str | None
    age: str | None
    dep_age: str | None
    salary: str | None
    reference: str | None  # absolute reference cell, e.g. "$J$1"


@dataclass
class Cover:
    """One person's cover under one product block."""

    info: CategoryInfo
    assignment: ListingAssignment | None
    family_group: str | None
    covered_dependants: list[Dependant]
    uw_case: UnderwritingCase | None = None


def number_text(value: float) -> str:
    """A number as a formula literal, never in scientific notation."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _plain(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    return safe_cell(str(value))


def age_formula(dob_col: str | None, row: int, reference: str | None, basis: str) -> str | None:
    if not dob_col or not reference:
        return None
    plus = "+1" if basis == "ANB" else ""
    # Blank (not #NUM!) for a date of birth after the reference date — a
    # dependant born after the benefit year started.
    dob = f"{dob_col}{row}"
    return f'=IF(OR({dob}="",{dob}>{reference}),"",DATEDIF({dob},{reference},"y"){plus})'


def _date_literal(value: date) -> str:
    return f"DATE({value.year},{value.month},{value.day})"


def product_age_basis(product: ProductInfo, ctx: ElContext) -> str:
    return product.rules.age_basis or ctx.age_basis


def _age_reference(
    product: ProductInfo, ctx: ElContext, refs: Refs, attrs: dict[str, Any]
) -> str | None:
    """The reviewed age reference date as a formula operand (row 1 = year start)."""
    rule = product.rules.age_reference
    if rule == "Member effective date":
        effective = roster_date(attrs.get("effective_date"))
        return _date_literal(effective) if isinstance(effective, date) else None
    start = product.period[0]
    if rule == "Product cover start" and start and start != ctx.reference_date:
        return _date_literal(start)
    return refs.reference


def product_age(
    product: ProductInfo, ctx: ElContext, row: int, refs: Refs,
    attrs: dict[str, Any], dependant: bool,
) -> str | None:
    """The member's age under this product's reviewed basis and reference date."""
    basis = product_age_basis(product, ctx)
    reference = _age_reference(product, ctx, refs, attrs)
    age_col = refs.dep_age if dependant else refs.age
    if age_col and basis == ctx.age_basis and reference == refs.reference:
        return f"={age_col}{row}"
    return age_formula(refs.dep_dob if dependant else refs.dob, row, reference, basis)


def employee_value(
    role: str, letter: str, emp: Employee, ctx: ElContext, row: int, refs: Refs, masked: bool
) -> Any:
    attrs = emp.attribute_values or {}
    if role == "staff_id":
        return _plain(emp.staff_id)
    if role == "name":
        return _plain(emp.employee_name)
    if role == "age":
        return age_formula(refs.dob, row, refs.reference, ctx.age_basis)
    if role == "location":
        return _plain(attrs.get("depot") or attrs.get("location"))
    if role == "category":
        return _plain(attrs.get("ghs_category") or attrs.get("category"))
    if role == "national_id":
        value = attrs.get("id_no")
        return _plain(mask_nric(value) if masked and value else value)
    if role in DATE_ROLES:
        return _date(attrs.get(_EMPLOYEE_ATTRS.get(role, role)))
    if role == "salary":
        number = attrs.get("salary")
        if number is None or number == "":
            return None
        try:
            return float(number)
        except (TypeError, ValueError):
            return _plain(number)
    attr = _EMPLOYEE_ATTRS.get(role)
    return _plain(attrs.get(attr)) if attr else None


def dependant_value(
    role: str, dep: Dependant, ctx: ElContext, row: int, refs: Refs, masked: bool
) -> Any:
    attrs = dep.attribute_values or {}
    if role == "age":
        return age_formula(refs.dep_dob, row, refs.reference, ctx.age_basis)
    if role == "date_of_birth":
        return _date(attrs.get("date_of_birth"))
    if role == "national_id":
        value = attrs.get("dependant_id_no")
        return _plain(mask_nric(value) if masked and value else value)
    attr = _DEPENDANT_ATTRS.get(role)
    return _plain(attrs.get(attr)) if attr else None


def _admin(cover: Cover, ctx: ElContext, above_limit: bool) -> str | None:
    listed = cover.assignment.admin_type if cover.assignment else None
    if listed:
        return "Named" if listed == "named" else "Headcount"
    if above_limit:
        return "Named"
    basis = ctx.products[cover.info.product.code].admin_basis.lower()
    if "name" in basis and "unname" not in basis:
        return "Named"
    return "Headcount" if basis else None


def needs_underwriting(cover: Cover, ctx: ElContext, salary: float | None,
                       anb: int | None) -> bool:
    """Above the non-evidence limit by sum insured or age (new cover / increase)."""
    product = ctx.products[cover.info.product.code]
    eligible = eligible_amount(cover.info, salary, product.max_si)
    present = (cover.assignment.recorded or {}).get("present_si") if cover.assignment else None
    increase = present is None or (eligible is not None and eligible > float(present))
    over_amount = bool(product.nel_amount and eligible and eligible > product.nel_amount)
    over_age = bool(product.nel_age and anb and anb >= product.nel_age)
    return increase and (over_amount or over_age)


def eligible_amount(info: CategoryInfo, salary: float | None, cap: float | None) -> float | None:
    amount: float | None
    if info.salary_multiple is not None:
        if salary is None:
            return None
        amount = info.salary_multiple * salary
    else:
        amount = info.flat_si
    if amount is None:
        return None
    return min(amount, cap) if cap else amount


def product_value(
    role: str,
    block: ElBlock,
    letters: dict[str, str],
    cover: Cover,
    ctx: ElContext,
    row: int,
    refs: Refs,
    *,
    dependant: Dependant | None,
    attrs: dict[str, Any],
    rate_cell: str | None,
    uw: bool,
) -> Any:
    info = cover.info
    product = ctx.products[info.product.code]
    recorded = (cover.assignment.recorded or {}) if cover.assignment else {}
    if role == "admin_type":
        return _admin(cover, ctx, uw and dependant is None)
    if role == "age":
        return product_age(product, ctx, row, refs, attrs, dependant is not None)
    if role == "category":
        return _plain(cover.assignment.listed_category if cover.assignment and
                      cover.assignment.listed_category else info.label)
    if role == "plan_type":
        if cover.assignment is not None:
            # The listing's own plan wording; a dependant row it left blank stays blank.
            listed = cover.assignment.listed_plan
            return _plain(listed or (None if dependant is not None else info.plan_code))
        return _plain(info.plan_code)
    if role == "family_group":
        return cover.family_group if dependant is None else None
    if dependant is not None and role not in {"premium", "premium_gst"}:
        return None
    if role == "eligible_si":
        cap = f",{number_text(product.max_si)}" if product.max_si else ""
        if info.salary_multiple is not None and refs.salary:
            core = f"{number_text(info.salary_multiple)}*{refs.salary}{row}"
            return f"=MIN({core}{cap})" if cap else f"={core}"
        if info.flat_si is not None:
            return min(info.flat_si, product.max_si) if product.max_si else info.flat_si
        return None
    if role == "pending_si":
        si, present = letters.get("eligible_si"), letters.get("present_si")
        if si and present and recorded.get("present_si") is not None:
            return f"=MAX({si}{row}-{present}{row},0)"
        return None
    if role in _RECORDED:
        value = recorded.get(role)
        return _date(value) if role in DATE_ROLES else _plain(value)
    if role == "premium":
        return _premium(cover, ctx, letters, row, dependant, rate_cell)
    if role == "premium_gst":
        premium = letters.get("premium")
        if not premium or product.gst_factor is None:
            return None
        if _premium(cover, ctx, letters, row, dependant, rate_cell) is None:
            return None
        return f"={premium}{row}*{number_text(product.gst_factor)}"
    return None


def billed_si(cover: Cover, ctx: ElContext, eligible: str) -> str:
    """The sum insured a per-$1,000 premium is billed on, as a formula operand.

    Eligible SI, unless the product's reviewed premium basis is Accepted SI:
    then the accepted SI that ``report_uw_amounts`` reports to insurers (a
    decision's accepted amount, an open case's guaranteed SI, else eligible SI
    up to the free cover limit). For any eligible amount that equals
    MIN(eligible, the amount accepted at an unlimited eligible SI), so the
    eligible SI stays a live formula.
    """
    info = cover.info
    if ctx.products[info.product.code].rules.premium_si_basis != "Accepted SI":
        return eligible
    _pending, limit = report_uw_amounts(math.inf, ctx.uw_fcls.get(info.product.id),
                                        cover.uw_case)
    return f"MIN({eligible},{number_text(limit)})" if math.isfinite(limit) else eligible


def _premium(
    cover: Cover, ctx: ElContext, letters: dict[str, str], row: int,
    dependant: Dependant | None, rate_cell: str | None,
) -> Any:
    info = cover.info
    if info.rate_basis == "per_1000_si":
        si = letters.get("eligible_si")
        if not si or dependant is not None or info.rate is None:
            return None
        rate = rate_cell or f"{number_text(info.rate)}/1000"
        return f"={rate}*{billed_si(cover, ctx, f'{si}{row}')}"
    if info.rate_basis == "tiered":
        if dependant is not None:
            return None
        return info.tiers.get(cover.family_group or "EO")
    if info.rate_basis in {"flat", "per_member"}:
        if dependant is not None and not info.covers_dependants:
            return None
        return info.rate
    return None
