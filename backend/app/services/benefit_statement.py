"""Assemble a read-only, benefits-only coverage statement for one employee.

Joins the employee's resolved categories → plans (via ``plan_hydration``) into
presentation-shaped coverage lines, and derives which of the employee's
dependants are covered per product.

Each coverage line carries the PER-MEMBER ``financials`` (the member's own Amount
Covered + premium — age-banded for voluntary life tiers, reflecting any elected
upgrade/downgrade), via ``plan_hydration.member_financials``. These are
per-employee figures, never the group sum-insured / total premium. When this view
is later split into an employee-facing statement, gate ``financials`` off there.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Dependant,
    Employee,
    EmployeeAttributeSchema,
    FlexScheme,
    PolicyYear,
    ProductSetup,
)
from app.models.category import Category
from app.models.product import Product
from app.schemas.api import (
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    FlexPriceTagLine,
    PlanFinancials,
    StatementAttribute,
    StatementEmployee,
)
from app.services.dependant_coverage import (
    category_covers_dependants as _shared_category_covers_dependants,
)
from app.services.dependant_coverage import (
    category_dependant_mode,
    has_member_cover_eligibility_answer,
)
from app.services.flex_membership import (
    classify_relationship,
    count_dependants,
    resolve_family_status,
)
from app.services.flex_pricing_resolver import summarize_employee
from app.services.flex_proration import proration_line
from app.services.member_premium import member_premium
from app.services.plan_hydration import basis_amount, hydrate_plans
from app.services.product_registry import get_entry
from app.services.roster_attributes import (
    DOB_KEYS,
    REL_KEYS,
    first_value,
    iso_date,
)
from app.services.roster_dedup import DEP_NAME_KEYS
from app.services.voluntary_enrolment import employee_participation, enrolled_products

# Attributes surfaced on the statement, in display order. Raw `category` plus the
# derived attributes the matching rules key on (see services/derivation_engine).
# `job_grade` is the field every CDL slip band keys on ("Job category: E1 to
# E6"), so a broker checking a plan needs it beside the roster category.
_KEY_ATTRS: tuple[str, ...] = (
    "category", "job_grade", "grade", "class", "pass", "family_status",
)
# Family-status codes (flex_membership.FAMILY_CODES) read as words.
_FAMILY_LABELS: dict[str, str] = {
    "S": "Single",
    "M": "Married",
    "M1C": "Family, 1 child",
    "M2C": "Family, 2 children",
    "M3C": "Family, 3+ children",
}

# Tolerant attribute-key lookup shared with the fact-find form.
#
# A DEPENDANT's name must be read through `DEP_NAME_KEYS`, never `NAME_KEYS`:
# the latter includes `employee_name`, and dependant rows genuinely carry that
# column (the parser writes it — `roster_parser.DEPENDANT_COLUMN_MAP`). So a row
# with no `dependant_name` was displaying the PARENT's name as the dependant's,
# to brokers and to the member on their own statement.
_NAME_KEYS = DEP_NAME_KEYS
_REL_KEYS = REL_KEYS
_DOB_KEYS = DOB_KEYS
_first = first_value


def _dep_summary(dep: Dependant) -> DependantSummary:
    av = dep.attribute_values or {}
    rel = _first(av, _REL_KEYS)
    return DependantSummary(
        id=dep.id,
        name=_first(av, _NAME_KEYS),
        relationship=rel,
        dob=iso_date(_first(av, _DOB_KEYS)),
        # Classified HERE, by the same function flex pricing uses, so the UI
        # never has to reimplement the word lists.
        role=classify_relationship(rel),
    )


def _category_covers_dependants(
    has_dependants: bool,
    plan_assignments: dict[str, Any] | None,
    participation_detail: dict[str, Any] | None = None,
    display_name: str | None = None,
    raw_description: str | None = None,
    *,
    legacy_product_default: bool = False,
) -> bool:
    return _shared_category_covers_dependants(
        has_dependants,
        plan_assignments,
        participation_detail,
        display_name,
        raw_description,
        legacy_product_default=legacy_product_default,
    )


def _attribute_labels(db: Session, client_id: str) -> dict[str, str]:
    rows = db.execute(
        select(EmployeeAttributeSchema.attribute_id, EmployeeAttributeSchema.display_name)
        .where(tenant_or_global(EmployeeAttributeSchema.client_id, client_id))
        # Global rows (client_id IS NULL → False) first, tenant rows last, so the
        # tenant-specific label deterministically wins the dict's last-write.
        .order_by(EmployeeAttributeSchema.client_id.isnot(None))
    ).all()
    return {aid: name for aid, name in rows}


def _find_tier(
    scheme: dict[str, Any], tier_name: str | None
) -> dict[str, Any] | None:
    """Locate the scheme tier the employee was assigned to, by name."""
    if not tier_name:
        return None
    for t in scheme.get("tiers") or []:
        if isinstance(t, dict) and str(t.get("name") or "") == tier_name:
            return t
    return None


def _naive(dt: datetime | None) -> datetime | None:
    """Drop tzinfo for a dialect-agnostic comparison (SQLite returns naive,
    Postgres aware — normalize so the two sides never mix)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _assignment_is_stale(
    scheme_row: FlexScheme, assigned_at: datetime | None, tier: dict[str, Any] | None
) -> bool:
    """True when the wallet snapshot may no longer reflect the scheme.

    Either the assigned tier no longer resolves (renamed/removed), or the scheme
    was edited after the wallet was assigned.
    """
    if tier is None:
        return True
    if assigned_at is None:
        return False
    updated = _naive(scheme_row.updated_at)
    stamped = _naive(assigned_at)
    return updated is not None and stamped is not None and updated > stamped


def _build_flex_coverage(db: Session, employee: Employee) -> FlexCoverageLine | None:
    """Assemble the employee's Flex wallet from the persisted snapshot + scheme.

    Returns None unless a Flex scheme still exists AND the employee carries an
    assigned wallet (``flex_tier_name``). The wallet figures come from the
    persisted ``flex_*`` columns (authoritative); the claimable categories and
    cost-share are read live from the scheme tier for display only.
    """
    if not employee.flex_tier_name:
        return None
    scheme_row = db.execute(
        select(FlexScheme).where(FlexScheme.policy_year_id == employee.policy_year_id)
    ).scalar_one_or_none()
    if scheme_row is None:
        return None

    scheme = scheme_row.scheme or {}
    raw_meta = scheme.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    from app.services.flex_age import employee_age_eligible

    year = db.get(PolicyYear, employee.policy_year_id)
    if not employee_age_eligible(employee, meta, year.start_date if year else None):
        return None
    tier = _find_tier(scheme, employee.flex_tier_name)

    categories: list[FlexBenefitCategoryLine] = []
    employer_pct: float | None = None
    employee_pct: float | None = None
    if tier is not None:
        for cat in tier.get("benefit_categories") or []:
            if not isinstance(cat, dict) or not str(cat.get("name") or "").strip():
                continue
            sub = cat.get("sub_limit")
            categories.append(FlexBenefitCategoryLine(
                name=str(cat["name"]),
                claimable=bool(cat.get("claimable", True)),
                sub_limit=float(sub) if isinstance(sub, (int, float)) else None,
                note=(str(cat["note"]) if cat.get("note") else None),
            ))
        cs = tier.get("cost_sharing")
        if isinstance(cs, dict):
            er, ee = cs.get("employer_pct"), cs.get("employee_pct")
            employer_pct = float(er) if isinstance(er, (int, float)) else None
            employee_pct = float(ee) if isinstance(ee, (int, float)) else None

    # Price tags: wallet spent to offset coverage + net balance (None when no matrix).
    summary = summarize_employee(db, employee)
    price_lines = (
        [
            FlexPriceTagLine(
                product_code=ln.product_code,
                plan_code=ln.plan_code,
                price_tag=ln.price_tag,
                dependant_tag=ln.dependant_tag,
            )
            for ln in summary.lines
        ]
        if summary is not None
        else []
    )

    return FlexCoverageLine(
        scheme_name=(str(meta.get("scheme_name")) if meta.get("scheme_name") else None),
        tier_name=employee.flex_tier_name,
        family_status=employee.flex_family_status,
        wallet_amount=employee.flex_wallet_amount,
        proration=proration_line(employee),
        currency=employee.flex_currency,
        source=employee.flex_source,
        employer_pct=employer_pct,
        employee_pct=employee_pct,
        benefit_categories=categories,
        price_tags_total=summary.total_price_tag if summary is not None else None,
        flex_balance=summary.balance if summary is not None else None,
        price_tag_lines=price_lines,
        price_age_known=summary.age_known if summary is not None else True,
        leave_action=summary.leave_action if summary is not None else None,
        leave_days=summary.leave_days if summary is not None else None,
        leave_flex_amount=summary.leave_flex_amount if summary is not None else None,
        assignment_stale=_assignment_is_stale(
            scheme_row, employee.flex_assigned_at, tier
        ),
    )


@dataclass(frozen=True)
class _CategoryFacts:
    product_id: str | None = None
    pa: dict[str, Any] | None = None
    rule: str | None = None
    employee_mode: str | None = None
    dependant_mode: str | None = None


_NO_FACTS = _CategoryFacts()


def _category_facts(
    db: Session, policy_year_id: str, cat_ids: list[str]
) -> dict[str, _CategoryFacts]:
    """What each matched category says about participation and dependants."""
    if not cat_ids:
        return {}
    rows = db.execute(
        select(Category, Product.has_dependants, Product.code)
        .outerjoin(Product, Category.product_id == Product.id)
        .where(Category.id.in_(cat_ids))
    ).all()
    codes = {str(code or "").strip().upper() for _cat, _has, code in rows}
    setup_has_member_cover = {
        str(code or "").strip().upper(): has_member_cover_eligibility_answer(answers)
        for code, answers in db.execute(
            select(ProductSetup.product_code, ProductSetup.answers).where(
                ProductSetup.policy_year_id == policy_year_id,
                ProductSetup.product_code.in_(codes),
            )
        ).all()
    }
    facts: dict[str, _CategoryFacts] = {}
    for cat, has_dep, code in rows:
        legacy_default = bool(has_dep) and not setup_has_member_cover.get(
            str(code or "").strip().upper(), False
        )
        facts[cat.id] = _CategoryFacts(
            product_id=cat.product_id,
            pa=cat.plan_assignments,
            rule=cat.rule_human_readable,
            employee_mode=employee_participation(cat),
            dependant_mode=category_dependant_mode(
                bool(has_dep),
                cat.plan_assignments,
                cat.participation_detail,
                cat.display_name,
                cat.raw_description,
                legacy_product_default=legacy_default,
            ),
        )
    return facts


def _member_line_financials(
    fin: PlanFinancials | None,
    pa: dict[str, Any] | None,
    covered_deps: list[DependantSummary],
) -> tuple[PlanFinancials | None, str | None]:
    """Only PER-MEMBER figures reach a coverage line.

    Sum-insured products arrive already reduced (``member_financials``). A flat
    or tiered reimbursement product is priced here from its per-head rate for
    the family actually covered. Anything else would carry the GROUP sum
    insured / total premium straight from the category, so it is suppressed
    rather than mislabelled as the member's.
    """
    if fin is None:
        return None, None
    pa = pa or {}
    if basis_amount(pa) is not None or pa.get("voluntary_rates"):
        return fin, None
    spouses = sum(1 for d in covered_deps if d.role == "spouse")
    children = sum(1 for d in covered_deps if d.role == "child")
    priced = member_premium(fin, spouses=spouses, children=children)
    if priced is None:
        return None, None
    tiers = (
        {k: {"rate": v["rate"]} for k, v in fin.rate_tiers.items() if "rate" in v}
        if fin.rate_tiers
        else None
    )
    return (
        fin.model_copy(
            update={
                "annual_premium": priced.amount,
                "sum_insured": None,
                "num_employees": None,
                "rate_tiers": tiers,
            }
        ),
        priced.note,
    )


def build_benefit_statement(db: Session, employee: Employee) -> BenefitStatementOut:
    matched_plans = hydrate_plans([employee], db, employee.policy_year_id).get(employee.id, [])

    cat_facts = _category_facts(
        db, employee.policy_year_id, [mp.category_id for mp in matched_plans if mp.category_id]
    )
    enrolled = enrolled_products(db, employee.policy_year_id, [employee.id])

    dependants = list(
        db.execute(
            select(Dependant).where(
                Dependant.employee_id == employee.id,
                Dependant.policy_year_id == employee.policy_year_id,
                # Portal self-added dependants are pending broker approval and
                # must not appear as covered or shift the family status.
                Dependant.status == "active",
            )
        ).scalars().all()
    )
    dep_summaries = [_dep_summary(d) for d in dependants]
    dep_by_id = {d.id: _dep_summary(d) for d in dependants}

    coverage: list[CoverageLine] = []
    for mp in matched_plans:
        facts = cat_facts.get(mp.category_id or "", _NO_FACTS)
        enrolment: Literal["covered", "eligible"] = (
            "eligible"
            if facts.employee_mode == "voluntary"
            and (employee.id, facts.product_id) not in enrolled
            else "covered"
        )
        dep_mode = facts.dependant_mode
        if enrolment == "eligible":
            # Not enrolled themselves, so no dependant can hold this cover yet.
            covered_deps: list[DependantSummary] = []
        elif mp.covered_dependant_ids is not None:
            # An override naming the enrolled dependants is authoritative.
            covered_deps = [dep_by_id[i] for i in mp.covered_dependant_ids if i in dep_by_id]
        else:
            covered_deps = dep_summaries if dep_mode == "compulsory" else []
        covered_ids = {d.id for d in covered_deps}
        eligible_deps = (
            [d for d in dep_summaries if d.id not in covered_ids] if dep_mode else []
        )
        fin, premium_note = _member_line_financials(mp.financials, facts.pa, covered_deps)
        coverage.append(CoverageLine(
            product_code=mp.product_code,
            product_name=mp.product_name,
            care_route=entry.care_route if (entry := get_entry(mp.product_code)) else None,
            category_id=mp.category_id,
            category_display=mp.category_display,
            match_method=mp.method,
            match_confidence=mp.confidence,
            rule_human_readable=facts.rule,
            plan_code=mp.plan_code,
            cover_description=mp.cover_description,
            annual_policy_limit=mp.annual_policy_limit,
            benefit_schedule=mp.benefit_schedule,
            plan_status=mp.plan_status,
            financials=fin,
            covers_dependants=bool(covered_deps),
            covered_dependants=covered_deps,
            enrolment=enrolment,
            dependant_cover=dep_mode,
            eligible_dependants=eligible_deps,
            product_id=facts.product_id,
            plan_overridden=bool(
                mp.plan_code
                and str(mp.plan_code) != str((facts.pa or {}).get("plan_code") or "")
            ),
            premium_note=premium_note,
        ))

    # Stable, predictable ordering for the UI.
    coverage.sort(key=lambda c: c.product_code)

    labels = _attribute_labels(db, employee.client_id)
    merged = {**(employee.attribute_values or {}), **(employee.derived_attribute_values or {})}
    # Resolve family status through the same resolver the Flex membership view
    # uses (dependant records first, then the roster), so the statement can't
    # diverge from the family-status counts. Only override when it resolves.
    spouse_count, child_count = count_dependants(dependants)
    resolved_fs, _ = resolve_family_status(
        employee.derived_attribute_values or {},
        employee.attribute_values or {},
        spouse_count,
        child_count,
        bool(dependants),
    )
    if resolved_fs:
        merged["family_status"] = resolved_fs
    attributes: list[StatementAttribute] = []
    for key in _KEY_ATTRS:
        val = merged.get(key)
        if val in (None, ""):
            continue
        attributes.append(StatementAttribute(
            key=key,
            label=labels.get(key) or key.replace("_", " ").title(),
            value=_FAMILY_LABELS.get(str(val), str(val)) if key == "family_status" else str(val),
        ))

    flex = _build_flex_coverage(db, employee)

    return BenefitStatementOut(
        employee=StatementEmployee(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        ),
        policy_year_id=employee.policy_year_id,
        is_matched=bool(coverage),
        attributes=attributes,
        coverage=coverage,
        dependants=dep_summaries,
        flex=flex,
    )
