"""Coverage limit checks: who crosses a product's slip limits this benefit year.

The placement slip caps who may be covered and for how much — "All full time &
permanent employees up to 75 ANB", "Last entry age: 70 ANB", spouse/child age
limits, the "Maximum Limit per Insured Person" and the Non-Evidence Limit. The
setup stores every one of them, but nothing applies the age limits to an
insured product, so a member past them stays covered silently.

This module reports, never enforces: every crossing becomes an alert the broker
resolves (end the cover, confirm an insurer arrangement, fix the roster). The
same list feeds the notification bell, launch readiness, the member coverage
filter and badges, and the live check beside the limits in product setup, so
those surfaces cannot disagree.

Ages are taken at the benefit year's start, in the product's age convention
(ANB unless the setup says ALB) — the reference underwriting and flex already
use. Dependants are checked only where coverage itself covers them
(``dependant_coverage.category_dependant_mode`` plus enrolment overrides).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Dependant,
    Employee,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
    UnderwritingCase,
)
from app.models.underwriting_case import normalize_uw_status
from app.schemas.coverage_limits import CoverageLimitAlert, LimitOverrides
from app.services.coverage_resolver import load_overrides
from app.services.dependant_coverage import (
    category_dependant_mode,
    has_member_cover_eligibility_answer,
)
from app.services.el_report_rules import ReportRules, mapping, positive_number
from app.services.flex_membership import classify_relationship
from app.services.plan_hydration import salary_from_attrs, salary_multiple
from app.services.roster_attributes import (
    DOB_KEYS,
    REL_KEYS,
    age_as_of,
    first_value,
    has_left,
    parse_dob,
    roster_date,
)
from app.services.roster_dedup import DEP_NAME_KEYS

LimitKind = Literal[
    "over_age",
    "over_entry_age",
    "dependant_over_age",
    "underwriting",
    "capped",
    "no_salary",
    "no_dob",
]

# Alerts that need the broker to act. "capped" states a fact the slip intends.
ACTION_KINDS: frozenset[str] = frozenset(
    {"over_age", "over_entry_age", "dependant_over_age", "underwriting", "no_salary", "no_dob"}
)
_UNDECIDED = frozenset({"pending", "postponed"})
_FIRST_INT = re.compile(r"\d+")


def _age_limit(raw: Any) -> int | None:
    """"75", "75 ANB", 75.0 → 75. Blank or wording without a number → None."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    match = _FIRST_INT.search(str(raw or ""))
    return int(match.group()) if match and int(match.group()) > 0 else None


def _money(value: float) -> str:
    return f"S${value:,.0f}"


def _day(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


@dataclass(frozen=True)
class ProductLimits:
    product_id: str
    code: str
    name: str
    employee_age_limit: int | None
    last_entry_age: int | None
    spouse_age_limit: int | None
    child_age_limit: int | None
    max_sum_insured: float | None
    entry_exempt: frozenset[str]
    alb: bool
    cover_start: date | None
    roles: frozenset[str]
    setup_has_member_cover: bool

    @property
    def unit(self) -> str:
        return "ALB" if self.alb else "ANB"

    def age(self, dob: date | None, ref: date) -> int | None:
        if dob is None:
            return None
        return age_as_of(dob, ref) + (0 if self.alb else 1)


def product_limits(
    db: Session,
    policy_year: PolicyYear,
    *,
    product_code: str | None = None,
    overrides: LimitOverrides | None = None,
) -> dict[str, ProductLimits]:
    """{product_id: limits} for every product with a setup in the year.

    ``overrides`` replaces the saved values for ``product_code`` — the setup
    form previews its unsaved limits through it.
    """
    setups = {
        s.product_code.upper(): s.answers if isinstance(s.answers, dict) else {}
        for s in db.scalars(
            select(ProductSetup).where(ProductSetup.policy_year_id == policy_year.id)
        )
    }
    wanted = product_code.upper() if product_code else None
    out: dict[str, ProductLimits] = {}
    for product, term in db.execute(
        select(Product, ProductTerm)
        .join(ProductTerm, ProductTerm.product_id == Product.id)
        .where(ProductTerm.policy_year_id == policy_year.id)
    ).all():
        code = product.code.upper()
        if wanted and code != wanted:
            continue
        answers = setups.get(code)
        if answers is None:
            continue
        eligibility = mapping(answers.get("eligibility"))
        header = mapping(answers.get("header"))
        values: dict[str, Any] = {
            "employee_age_limit": eligibility.get("employee_age_limit"),
            "last_entry_age": eligibility.get("last_entry_age"),
            "spouse_age_limit": eligibility.get("spouse_age_limit"),
            "child_age_limit": eligibility.get("child_age_limit"),
            "max_sum_insured": header.get("el_max_sum_insured"),
            "employees_above_last_entry_age": eligibility.get("employees_above_last_entry_age"),
        }
        if overrides is not None and wanted == code:
            values.update(overrides.model_dump(exclude_unset=True))
        exempt = {
            token.strip().upper()
            for token in str(values.get("employees_above_last_entry_age") or "").split(",")
            if token.strip()
        }
        roles = {
            token.strip()
            for token in str(eligibility.get("member_cover_eligibility") or "").split(",")
            if token.strip()
        }
        out[product.id] = ProductLimits(
            product_id=product.id,
            code=product.code,
            name=product.display_name or product.code,
            employee_age_limit=_age_limit(values["employee_age_limit"]),
            last_entry_age=_age_limit(values["last_entry_age"]),
            spouse_age_limit=_age_limit(values["spouse_age_limit"]),
            child_age_limit=_age_limit(values["child_age_limit"]),
            max_sum_insured=positive_number(values["max_sum_insured"]),
            entry_exempt=frozenset(exempt),
            alb=ReportRules.from_answers(answers).age_basis == "ALB",
            cover_start=term.coverage_start,
            roles=frozenset(roles),
            setup_has_member_cover=has_member_cover_eligibility_answer(answers),
        )
    return out


def _employee_alerts(
    emp: Employee,
    limits: ProductLimits,
    category: Category,
    ref: date,
) -> list[CoverageLimitAlert]:
    attrs = emp.attribute_values or {}
    staff = str(emp.staff_id or "")

    def alert(
        kind: LimitKind,
        message: str,
        *,
        severity: Literal["action", "info"] = "action",
        age: int | None = None,
        limit: float | None = None,
        amount: float | None = None,
    ) -> CoverageLimitAlert:
        return CoverageLimitAlert(
            kind=kind, severity=severity, product_code=limits.code,
            product_name=limits.name, employee_id=emp.id, staff_id=staff,
            employee_name=emp.employee_name, age=age, limit=limit, amount=amount,
            message=message,
        )

    alerts: list[CoverageLimitAlert] = []
    dob = parse_dob(first_value(attrs, DOB_KEYS))
    age = limits.age(dob, ref)
    if age is None and (limits.employee_age_limit or limits.last_entry_age):
        alerts.append(alert(
            "no_dob",
            "No date of birth on file, so the age limits can't be checked.",
        ))
    if age is not None and limits.employee_age_limit and age > limits.employee_age_limit:
        alerts.append(alert(
            "over_age",
            f"{age} {limits.unit} at {_day(ref)}, above the age limit of "
            f"{limits.employee_age_limit} {limits.unit}. Cover should end unless the "
            "insurer has agreed to continue it.",
            age=age,
            limit=float(limits.employee_age_limit),
        ))
    hired = roster_date(attrs.get("date_of_hire"))
    if (
        isinstance(hired, date)
        and dob is not None
        and limits.last_entry_age
        and limits.cover_start is not None
        and hired >= limits.cover_start
        and staff.upper() not in limits.entry_exempt
    ):
        entry_age = limits.age(dob, hired)
        if entry_age is not None and entry_age > limits.last_entry_age:
            alerts.append(alert(
                "over_entry_age",
                f"Joined {_day(hired)} at {entry_age} {limits.unit}, above the last "
                f"entry age of {limits.last_entry_age} {limits.unit}. If the insurer "
                "accepts them, add their staff ID under Employees Above Last Entry Age.",
                age=entry_age,
                limit=float(limits.last_entry_age),
            ))
    pa = category.plan_assignments if isinstance(category.plan_assignments, dict) else {}
    multiple = salary_multiple(pa)
    if multiple is not None:
        salary = salary_from_attrs(attrs)
        if salary is None:
            alerts.append(alert(
                "no_salary",
                f"No salary on file, so {pa.get('basis')} can't be worked out.",
            ))
        elif limits.max_sum_insured is not None:
            mult, annual = multiple
            uncapped = salary * (12.0 if annual else 1.0) * mult
            if uncapped > limits.max_sum_insured:
                alerts.append(alert(
                    "capped",
                    f"Cover capped at {_money(limits.max_sum_insured)}; "
                    f"{pa.get('basis')} would be {_money(uncapped)}.",
                    severity="info",
                    amount=uncapped,
                    limit=limits.max_sum_insured,
                ))
    return alerts


def _dependant_alerts(
    emp: Employee,
    deps: Iterable[Dependant],
    limits: ProductLimits,
    ref: date,
) -> list[CoverageLimitAlert]:
    alerts: list[CoverageLimitAlert] = []
    for dep in deps:
        attrs = dep.attribute_values or {}
        role = classify_relationship(first_value(attrs, REL_KEYS))
        limit = {"spouse": limits.spouse_age_limit, "child": limits.child_age_limit}.get(role or "")
        if not limit:
            continue
        age = limits.age(parse_dob(first_value(attrs, DOB_KEYS)), ref)
        if age is None or age <= limit:
            continue
        name = first_value(attrs, DEP_NAME_KEYS) or "Dependant"
        alerts.append(CoverageLimitAlert(
            kind="dependant_over_age", severity="action",
            product_code=limits.code, product_name=limits.name,
            employee_id=emp.id, staff_id=str(emp.staff_id or ""),
            employee_name=emp.employee_name, dependant_id=dep.id,
            dependant_name=name, relationship=role, age=age, limit=float(limit),
            message=(
                f"{role.capitalize() if role else 'Dependant'} {name} is {age} {limits.unit}, "
                f"above the {role} age limit of {limit} {limits.unit}."
            ),
        ))
    return alerts


def _underwriting_alerts(
    db: Session,
    policy_year_id: str,
    limits: dict[str, ProductLimits],
    employees: dict[str, Employee],
    dependants: dict[str, Dependant],
) -> list[CoverageLimitAlert]:
    alerts: list[CoverageLimitAlert] = []
    for case in db.scalars(
        select(UnderwritingCase).where(
            UnderwritingCase.policy_year_id == policy_year_id,
            UnderwritingCase.product_id.in_(list(limits)),
        )
    ):
        if normalize_uw_status(case.status) not in _UNDECIDED:
            continue
        dep = dependants.get(case.dependant_id or "")
        emp = employees.get(case.employee_id or (dep.employee_id if dep else None) or "")
        if emp is None:
            continue
        dep_attrs = (dep.attribute_values or {}) if dep is not None else {}
        lim = limits[case.product_id]
        eligible = float(case.eligible_si or 0)
        guaranteed = float(case.guaranteed_si or 0)
        alerts.append(CoverageLimitAlert(
            kind="underwriting", severity="action",
            product_code=lim.code, product_name=lim.name,
            employee_id=emp.id, staff_id=str(emp.staff_id or ""),
            employee_name=emp.employee_name, dependant_id=case.dependant_id,
            dependant_name=first_value(dep_attrs, DEP_NAME_KEYS) if dep else None,
            relationship=(
                classify_relationship(first_value(dep_attrs, REL_KEYS)) if dep else None
            ),
            amount=eligible - guaranteed, limit=guaranteed,
            message=(
                f"Needs underwriting for {_money(eligible - guaranteed)} above the "
                f"{_money(guaranteed)} covered without evidence ({_money(eligible)} eligible)."
            ),
        ))
    return alerts


def coverage_limit_alerts(
    db: Session,
    policy_year: PolicyYear,
    *,
    employee_ids: set[str] | None = None,
    product_code: str | None = None,
    overrides: LimitOverrides | None = None,
) -> list[CoverageLimitAlert]:
    """Every limit a current member crosses this benefit year."""
    ref = policy_year.start_date
    limits = product_limits(db, policy_year, product_code=product_code, overrides=overrides)
    if ref is None or not limits:
        return []
    stmt = select(Employee).where(Employee.policy_year_id == policy_year.id)
    if employee_ids is not None:
        stmt = stmt.where(Employee.id.in_(employee_ids))
    employees = {e.id: e for e in db.scalars(stmt) if not has_left(e)}
    if not employees:
        return []
    overrides_by = load_overrides(db, policy_year.id, list(employees))
    cat_ids = {
        str(m.get("category_id"))
        for e in employees.values()
        for m in e.matched_categories or []
        if m.get("category_id")
    } | {o.tier_category_id for o in overrides_by.values() if o.tier_category_id}
    categories = {
        c.id: c
        for c in db.scalars(select(Category).where(Category.id.in_(cat_ids)))
        if c.product_id in limits
    }
    has_dependants = {
        pid: bool(flag)
        for pid, flag in db.execute(
            select(Product.id, Product.has_dependants).where(Product.id.in_(list(limits)))
        ).all()
    }
    deps_by_emp: dict[str, list[Dependant]] = {}
    dependants: dict[str, Dependant] = {}
    for dep in db.scalars(
        select(Dependant).where(
            Dependant.policy_year_id == policy_year.id,
            Dependant.employee_id.in_(list(employees)),
            Dependant.status == "active",
        )
    ):
        if dep.employee_id:
            deps_by_emp.setdefault(dep.employee_id, []).append(dep)
            dependants[dep.id] = dep

    alerts: list[CoverageLimitAlert] = []
    for emp in employees.values():
        for match in emp.matched_categories or []:
            category = categories.get(str(match.get("category_id") or ""))
            if category is None or category.product_id is None:
                continue
            lim = limits[category.product_id]
            override = overrides_by.get((emp.id, category.product_id))
            if override is not None and override.declined:
                continue
            # The basis follows the tier the member elected, as coverage does.
            priced = categories.get(override.tier_category_id or "") if override else None
            alerts.extend(_employee_alerts(emp, lim, priced or category, ref))
            if not (lim.spouse_age_limit or lim.child_age_limit):
                continue
            mode = category_dependant_mode(
                has_dependants.get(category.product_id, False),
                category.plan_assignments,
                category.participation_detail,
                category.display_name,
                category.raw_description,
                legacy_product_default=(
                    has_dependants.get(category.product_id, False)
                    and not lim.setup_has_member_cover
                ),
            )
            household = deps_by_emp.get(emp.id, [])
            if override is not None and override.covered_dependant_ids is not None:
                named = set(override.covered_dependant_ids)
                covered = [d for d in household if d.id in named]
            else:
                covered = household if mode == "compulsory" else []
            alerts.extend(_dependant_alerts(emp, covered, lim, ref))
    alerts.extend(
        _underwriting_alerts(db, policy_year.id, limits, employees, dependants)
    )
    return alerts


def limit_counts(alerts: Iterable[CoverageLimitAlert]) -> dict[str, int]:
    """People affected per kind (a person crossing three products counts once)."""
    people: dict[str, set[str]] = {}
    for alert in alerts:
        people.setdefault(alert.kind, set()).add(alert.dependant_id or alert.employee_id)
    return {kind: len(ids) for kind, ids in people.items()}
