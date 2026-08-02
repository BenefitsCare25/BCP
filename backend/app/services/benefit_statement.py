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

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import Dependant, Employee, EmployeeAttributeSchema, FlexScheme
from app.models.category import Category
from app.models.product import Product
from app.schemas.api import (
    BenefitStatementOut,
    CoverageLine,
    DependantSummary,
    FlexBenefitCategoryLine,
    FlexCoverageLine,
    FlexPriceTagLine,
    StatementAttribute,
    StatementEmployee,
)
from app.services.flex_membership import (
    classify_relationship,
    count_dependants,
    resolve_family_status,
)
from app.services.flex_pricing_resolver import summarize_employee
from app.services.plan_hydration import basis_amount, hydrate_plans
from app.services.roster_attributes import (
    DOB_KEYS,
    NAME_KEYS,
    REL_KEYS,
    first_value,
    iso_date,
)

# Attributes surfaced on the statement, in display order. Raw `category` plus the
# derived attributes the matching rules key on (see services/derivation_engine).
_KEY_ATTRS: tuple[str, ...] = ("category", "grade", "class", "pass", "family_status")

# Employee-only tier codes. Any tier on a plan's menu OTHER than these signals
# the product extends beyond the employee (handles non-standard family labels
# like "Family"/"M+C", not just the canonical ES/EC/EF).
_EMPLOYEE_ONLY_TIERS = {"EO", "E", "EE", "EMPLOYEE", "EMPLOYEE ONLY"}

# Negated mention of dependants ("no dependant cover", "excluding dependants").
_NEG_DEPENDANT = re.compile(
    r"(?:\bno\b|\bnot\b|\bnon[-\s]?|\bwithout\b|\bexcl)[\w\s.,/-]{0,15}depend", re.I
)

# Tolerant attribute-key lookup shared with the fact-find form.
_NAME_KEYS = NAME_KEYS
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
    plan_assignments: dict | None,
    display_name: str | None,
    raw_description: str | None,
) -> bool:
    """Best-available signal that a product/category extends to dependants.

    There is no per-employee tier election stored anywhere, so this is a
    product-level determination: the product supports dependants AND either its
    plan tier menu includes a multi-member tier, or the category text names
    dependants. Conservative default is False. Refine here if an explicit
    per-employee tier ever gets captured.
    """
    if not has_dependants:
        return False
    pa = plan_assignments or {}
    for tier_field in ("rate_tiers", "tier_counts"):
        tiers = pa.get(tier_field)
        if isinstance(tiers, dict) and any(
            str(k).strip().upper() not in _EMPLOYEE_ONLY_TIERS for k in tiers
        ):
            return True
    text = f"{display_name or ''} {raw_description or ''}".lower()
    if "depend" not in text:
        return False
    # Don't count a negated mention ("no dependant cover") as coverage.
    return not _NEG_DEPENDANT.search(text)


def _attribute_labels(db: Session, client_id: str) -> dict[str, str]:
    rows = db.execute(
        select(EmployeeAttributeSchema.attribute_id, EmployeeAttributeSchema.display_name)
        .where(tenant_or_global(EmployeeAttributeSchema.client_id, client_id))
        # Global rows (client_id IS NULL → False) first, tenant rows last, so the
        # tenant-specific label deterministically wins the dict's last-write.
        .order_by(EmployeeAttributeSchema.client_id.isnot(None))
    ).all()
    return {aid: name for aid, name in rows}


def _find_tier(scheme: dict, tier_name: str | None) -> dict | None:
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
    scheme_row: FlexScheme, assigned_at: datetime | None, tier: dict | None
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
    meta = scheme.get("meta") if isinstance(scheme.get("meta"), dict) else {}
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


def build_benefit_statement(db: Session, employee: Employee) -> BenefitStatementOut:
    matched_plans = hydrate_plans([employee], db, employee.policy_year_id).get(employee.id, [])

    # Per-category dependant-coverage facts (product.has_dependants + plan_assignments + text).
    cat_ids = [mp.category_id for mp in matched_plans if mp.category_id]
    cat_facts: dict[str, tuple[bool, dict | None, str | None, str | None, str | None]] = {}
    if cat_ids:
        rows = db.execute(
            select(
                Category.id,
                Product.has_dependants,
                Category.plan_assignments,
                Category.display_name,
                Category.raw_description,
                Category.rule_human_readable,
            )
            .outerjoin(Product, Category.product_id == Product.id)
            .where(Category.id.in_(cat_ids))
        ).all()
        cat_facts = {
            cid: (bool(has_dep), pa, disp, raw, rule)
            for cid, has_dep, pa, disp, raw, rule in rows
        }

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
        has_dep, pa, disp, raw, rule = cat_facts.get(
            mp.category_id or "", (False, None, None, None, None)
        )
        # An override with explicit elected dependants is authoritative; otherwise
        # fall back to the product-level heuristic.
        if mp.covered_dependant_ids is not None:
            covered_deps = [dep_by_id[i] for i in mp.covered_dependant_ids if i in dep_by_id]
            covers = bool(covered_deps)
        else:
            covers = _category_covers_dependants(has_dep, pa, disp, raw)
            covered_deps = dep_summaries if covers else []
        # Only surface PER-MEMBER figures. A line that doesn't reduce to a
        # per-member sum assured (tiered medical, salary-multiple basis) would
        # otherwise carry the GROUP sum_insured / total premium / rate_tiers
        # straight from the category — suppress it rather than mislabel a group
        # total as the member's. (Reducibility is a cohort-level property, so the
        # matched category's basis/voluntary_rates settles it for elected tiers too.)
        fin = mp.financials
        if fin is not None and basis_amount(pa or {}) is None and not (
            pa or {}
        ).get("voluntary_rates"):
            fin = None
        coverage.append(CoverageLine(
            product_code=mp.product_code,
            product_name=mp.product_name,
            category_id=mp.category_id,
            category_display=mp.category_display,
            match_method=mp.method,
            match_confidence=mp.confidence,
            rule_human_readable=rule,
            plan_code=mp.plan_code,
            cover_description=mp.cover_description,
            annual_policy_limit=mp.annual_policy_limit,
            benefit_schedule=mp.benefit_schedule,
            financials=fin,
            covers_dependants=covers,
            covered_dependants=covered_deps,
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
            value=str(val),
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
