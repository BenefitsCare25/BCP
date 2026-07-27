"""Resolve an employee's matched categories into hydrated plan + SOB data.

Extracted from ``api/v1/employees.py`` so both the employee endpoints and the
benefit-statement service share one source of truth for the category → product →
plan traversal (and stay free of an import cycle).
"""
from __future__ import annotations

import logging
import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Employee, Plan, PolicyYear
from app.models.category import Category
from app.models.product import Product
from app.schemas.api import MatchedPlan, PlanFinancials, VoluntaryRateBand
from app.services.coverage_resolver import load_overrides, resolve_plan
from app.services.product_terms import product_gst_multipliers
from app.services.roster_attributes import age_from_attrs, band_for_age, first_value

logger = logging.getLogger(__name__)

# Group-aggregate / flat-rate fields that are meaningless on an age-banded
# voluntary tier (its premium is basis / 1000 x rate[member's age band], so a
# single rate, a group sum insured, a headcount or a tier table don't apply).
# Shared by the parser persistence (placement_slips._build_plan_assignments) and
# the backfill so the two can't drift on which fields to drop.
GROUP_RATE_FIELDS: tuple[str, ...] = (
    "num_employees",
    "sum_insured",
    "premium_rate",
    "annual_premium",
    "rate_tiers",
)


def hydrate_plans(
    employees: list[Employee],
    db: Session,
    policy_year_id: str | None = None,
) -> dict[str, list[MatchedPlan]]:
    """Build ``{employee_id: [MatchedPlan, …]}`` from ``matched_categories`` JSON.

    Does bulk queries for referenced categories, products, and plans to
    avoid N+1.
    """
    all_cat_ids: set[str] = set()
    for emp in employees:
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            if cid:
                all_cat_ids.add(cid)

    if not all_cat_ids:
        return {}

    # Sparse per-employee overrides (enrollment elections / bulk updates / manual
    # admin edits) that deviate from the cohort default. Loaded up front so the
    # elected tier's own category can be bulk-loaded too — a member who elected a
    # voluntary upgrade/downgrade is priced off THAT tier's plan_assignments
    # (its age-banded rate + basis), not the matched compulsory baseline's.
    overrides = (
        load_overrides(db, policy_year_id, [e.id for e in employees])
        if policy_year_id
        else {}
    )
    for ov in overrides.values():
        if ov.tier_category_id:
            all_cat_ids.add(ov.tier_category_id)

    rows = db.execute(
        select(
            Category.id,
            Category.display_name,
            Category.plan_assignments,
            Category.product_id,
            Product.code,
            Product.display_name,
        )
        .outerjoin(Product, Category.product_id == Product.id)
        .where(Category.id.in_(all_cat_ids))
    ).all()
    cat_info: dict[str, tuple[str | None, dict | None, str | None, str | None, str | None]] = {
        cid: (cat_disp, pa, prod_id, pcode, pname)
        for cid, cat_disp, pa, prod_id, pcode, pname in rows
    }

    # Bulk-load Plan records for benefit schedule data — only when at least one
    # referenced category maps to a product (otherwise there's nothing to resolve
    # and the SELECT + JSON deserialization is pure waste).
    plan_lookup: dict[tuple[str, str], Plan] = {}
    # Defensive fallback: a product with exactly one plan is its de-facto schedule
    # for a category that names NO plan at all. A category that names a specific
    # (but missing) plan is NOT given this plan — see the resolution loop below.
    sole_plan_by_product: dict[str, Plan] = {}
    if policy_year_id and any(prod_id for _, _, prod_id, _, _ in cat_info.values()):
        plan_rows = list(
            db.execute(
                select(Plan).where(Plan.policy_year_id == policy_year_id)
            ).scalars().all()
        )
        plan_lookup = {(p.product_id, p.code): p for p in plan_rows}
        plans_by_product: dict[str, list[Plan]] = {}
        for p in plan_rows:
            plans_by_product.setdefault(p.product_id, []).append(p)
        sole_plan_by_product = {
            pid: rows[0] for pid, rows in plans_by_product.items() if len(rows) == 1
        }

    # Ages drive voluntary life-tier premiums (age-banded). Compute as of the
    # policy year start (the carrier's reference), once per request.
    ref = _reference_date(db, policy_year_id)

    # Per-product GST gross-up (ProductTerm config): slip-extracted premiums are
    # GST-exclusive; the per-member figures surfaced here gross up once.
    gst_by_product = (
        product_gst_multipliers(db, policy_year_id) if policy_year_id else {}
    )

    result: dict[str, list[MatchedPlan]] = {}
    dangling = 0
    for emp in employees:
        emp_age = _employee_age(emp, ref)
        matched: list[MatchedPlan] = []
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            info = cat_info.get(cid) if cid else None
            if cid and info is None:
                # The matched category no longer exists (deleted by a slip
                # re-parse). Rendering it would produce a ghost coverage line
                # (stale product code, no plan/SOB/financials) on broker AND
                # member statements — skip it; re-running matching heals it.
                dangling += 1
                continue
            cat_disp, pa, prod_id, pcode, pname = info if info else (None, None, None, None, None)

            category_plan_code = pa.get("plan_code") if pa else None
            override = overrides.get((emp.id, prod_id)) if prod_id else None
            resolved = resolve_plan(override, category_plan_code)
            # Per-EMPLOYEE read path: surface the member's own premium, not the
            # group aggregate stored on the category (sum_insured / annual_premium
            # = num_employees x basis). member_financials reduces per_1000_si to
            # basis / 1000 x rate, and age-bands voluntary life tiers by emp_age.
            # When the member ELECTED a different tier (override carries its
            # category id), price off that tier's plan_assignments — its
            # age-banded voluntary rate + basis — instead of the matched baseline.
            fin_pa = pa
            if override and not resolved.declined and override.tier_category_id:
                elected = cat_info.get(override.tier_category_id)
                if elected and elected[1]:
                    fin_pa = elected[1]
                else:
                    # The elected tier's category was deleted (re-parse) — the
                    # member's premium silently reverts to the baseline tier's
                    # pricing. Surface it: this is a repricing, not a no-op.
                    logger.warning(
                        "Employee %s override for product %s elects tier "
                        "category %s which no longer exists — pricing falls "
                        "back to the baseline category.",
                        emp.id, prod_id, override.tier_category_id,
                    )
            fin = member_financials(fin_pa, emp_age) if fin_pa else None
            if fin is not None and prod_id in gst_by_product:
                fin = apply_gst_to_financials(fin, gst_by_product[prod_id])
            # A declined override means the member opted out of this product — drop
            # the line entirely so it never shows as covered.
            if resolved.declined:
                continue
            plan_code = resolved.plan_code
            benefit_schedule = None
            cover_description = None
            annual_policy_limit = None
            if prod_id:
                if plan_code:
                    # Names a specific plan: resolve it exactly. If it's missing
                    # (a genuine mismatch), leave the SOB blank so the gap shows —
                    # don't silently substitute a different plan's schedule.
                    plan_rec = plan_lookup.get((prod_id, str(plan_code)))
                else:
                    # Names no plan: a single-plan product is its de-facto schedule.
                    plan_rec = sole_plan_by_product.get(prod_id)
                if plan_rec:
                    benefit_schedule = plan_rec.benefit_schedule
                    cover_description = plan_rec.cover_description
                    annual_policy_limit = plan_rec.annual_policy_limit

            matched.append(MatchedPlan(
                product_code=pcode or m.get("product_code", "?"),
                product_name=pname,
                category_id=cid,
                plan_code=plan_code,
                category_display=cat_disp,
                method=m.get("method"),
                confidence=m.get("confidence"),
                financials=fin,
                benefit_schedule=benefit_schedule,
                cover_description=cover_description,
                annual_policy_limit=annual_policy_limit,
                plan_overridden=resolved.overridden,
                override_source=resolved.override_source,
                covered_dependant_ids=resolved.covered_dependant_ids,
            ))
        if matched:
            result[emp.id] = matched
    if dangling:
        logger.warning(
            "hydrate_plans: skipped %d dangling matched_categories entr%s "
            "(categories deleted since the last matching run) — re-run "
            "matching for policy year %s.",
            dangling, "y" if dangling == 1 else "ies", policy_year_id,
        )
    return result


def build_financials(pa: dict) -> PlanFinancials | None:
    """Convert ``plan_assignments`` dict to ``PlanFinancials`` if it has financial data."""
    has_data = any(
        pa.get(k) is not None
        for k in ("sum_insured", "premium_rate", "annual_premium", "rate_tiers",
                  "num_employees", "basis", "dependant_rate", "voluntary_rates")
    )
    if not has_data:
        return None
    bands_raw = pa.get("voluntary_rates")
    bands: list[VoluntaryRateBand] | None = None
    if bands_raw:
        bands = []
        for b in bands_raw:
            try:
                bands.append(VoluntaryRateBand.model_validate(b))
            except Exception as exc:
                # This feeds member premiums — a dropped band means a missing
                # rate row on statements. Never drop it silently.
                logger.warning(
                    "Dropping invalid voluntary rate band %r from "
                    "plan_assignments: %s", b, exc,
                )
                continue
    return PlanFinancials(
        num_employees=pa.get("num_employees"),
        basis=pa.get("basis"),
        sum_insured=pa.get("sum_insured"),
        premium_rate=pa.get("premium_rate"),
        annual_premium=pa.get("annual_premium"),
        rate_basis=pa.get("rate_basis"),
        rate_tiers=pa.get("rate_tiers"),
        dependant_rate=pa.get("dependant_rate"),
        estimated_annual_earnings=pa.get("estimated_annual_earnings"),
        voluntary_rates=bands or None,
    )


def _reference_date(db: Session, policy_year_id: str | None) -> date:
    """Age reference — the policy year's start (today as a safe fallback)."""
    if policy_year_id:
        py = db.get(PolicyYear, policy_year_id)
        if py and py.start_date:
            return py.start_date
    return date.today()


def _employee_age(employee: Employee, ref: date) -> int | None:
    """Member's age (last birthday) as of ``ref`` from their DOB, or None."""
    return age_from_attrs(employee.attribute_values, ref)


def member_age(db: Session, employee: Employee) -> int | None:
    """Member's age as of their policy year's start — for single-employee callers
    (the bulk ``hydrate_plans`` path computes the reference date once instead)."""
    return _employee_age(employee, _reference_date(db, employee.policy_year_id))


def basis_amount(pa: dict) -> float | None:
    """Per-member sum assured from ``basis`` — but only when it's a plain amount.

    ``basis`` can also be a salary-multiple expression ('12 times basic monthly
    salary') or a relative one ('50% of GTL'), which has no per-member number
    until a salary / linked SI is applied → None.
    """
    b = pa.get("basis")
    if isinstance(b, (int, float)):
        return float(b)
    if isinstance(b, str):
        try:
            return float(b.strip())
        except ValueError:
            return None
    return None


# "36 times basic monthly salary" / "24x basic monthly salary" / "2 X annual
# salary" — the leading number is the multiple, and the phrase it qualifies is
# captured with it. Anchoring on the salary phrase matters twice over: a bare
# "(\d+)\s*(x|times)" latches onto the digits inside a grouped amount ("S$100,000
# x 2" → "000 x" → multiple 0), and reading "annual" from the WHOLE basis string
# mis-scales a compound basis ("24 times basic monthly salary or 2 times annual
# salary" would gross the 24x by 12).
_SALARY_MULTIPLE = re.compile(
    r"(?<![\d,.])(\d+(?:\.\d+)?)\s*(?:x|times)\s+([a-z\s']{0,40}?salary)",
    re.IGNORECASE,
)

# Roster key spellings for the member's monthly salary (roster_parser maps
# "Monthly Salary" → "salary"; the others tolerate hand-built rosters).
SALARY_KEYS: tuple[str, ...] = ("salary", "monthly_salary", "basic_salary")


def salary_from_attrs(attribute_values: dict | None) -> float | None:
    """Monthly salary as a number from a roster ``attribute_values`` blob.

    Roster cells arrive as floats or display strings ("5,500", "S$5,500.00");
    strip currency/grouping noise before parsing. None when absent/unparseable.
    """
    raw = first_value(attribute_values or {}, SALARY_KEYS)
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9.]", "", raw)
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value > 0 else None


def salary_multiple(pa: dict) -> tuple[float, bool] | None:
    """``(multiple, is_annual)`` from an 'N times/x … salary' basis, else None.

    ``is_annual`` is read from the phrase THIS multiple qualifies, not the whole
    basis string, so a compound basis quoting both a monthly and an annual
    multiple can't cross-contaminate. A non-positive multiple is rejected: it
    can only come from a misparse, and returning 0 would publish a $0 sum
    insured as if it were a real figure.
    """
    b = pa.get("basis")
    if not isinstance(b, str) or "salary" not in b.lower():
        return None
    m = _SALARY_MULTIPLE.search(b)
    if m is None:
        return None
    mult = float(m.group(1))
    if mult <= 0:
        return None
    return mult, "annual" in m.group(2).lower()


def resolve_basis_amount(pa: dict, attribute_values: dict | None) -> float | None:
    """Per-member sum assured: a plain-amount basis, else a salary-multiple
    basis resolved against the member's roster monthly salary.

    An 'annual salary' multiple applies to 12x the monthly figure (rosters
    store monthly). None when the basis is relative ('50% of GTL'), tiered
    medical, or the member has no salary on file.
    """
    amount = basis_amount(pa)
    if amount is not None:
        return amount
    parsed = salary_multiple(pa)
    if parsed is None:
        return None
    mult, is_annual = parsed
    salary = salary_from_attrs(attribute_values)
    if salary is None:
        return None
    if is_annual:
        salary *= 12.0
    return salary * mult


def voluntary_rate_for_age(bands: list | None, age: int | None) -> float | None:
    """Per-S$1000 rate for ``age`` from a voluntary age-band table, or None when no
    age / no band covers it. Band selection is shared with the flex price-tag bands
    via ``band_for_age`` so premium + price tag never disagree on the member's band."""
    band = band_for_age(bands, age)
    if band is None:
        return None
    rate = band.get("rate")
    return float(rate) if isinstance(rate, (int, float)) else None


def member_financials(pa: dict, age: int | None = None) -> PlanFinancials | None:
    """Per-MEMBER view of a category's financials for any per-employee read path.

    ``sum_insured`` / ``annual_premium`` in ``plan_assignments`` are GROUP
    aggregates (``num_employees x basis``), computed once on the compulsory tier
    and copied to its voluntary siblings — so they're identical across a cohort
    and far larger than one member's figures. A member holds their own ``basis``
    (the sum assured), so reduce to per member:

    - **Voluntary life tier** (``plan_assignments`` carries a ``voluntary_rates``
      age-band table): the premium is age-banded — ``annual_premium`` = basis /
      1000 x rate[member's age band], and ``premium_rate`` is surfaced as that
      band's rate. Without an ``age`` (an aggregate view) the premium can't be
      pinned, so ``annual_premium`` is None.
    - ``per_1000_si`` with a numeric basis → ``sum_insured`` = basis and
      ``annual_premium`` = basis / 1000 x rate (the carrier's per-mille formula
      for compulsory GCI / GTL / GPA). Works regardless of any stale group
      SI/premium that matching attached, because it recomputes from the basis.
    - otherwise (salary-multiple / relative basis, tiered medical, no rate) →
      not reducible to one member here; return the parsed figures unchanged.

    Single source of truth shared by the employee endpoints, the benefit
    statement, and the enrollment election options (``cohort_tiers``).
    """
    fin = build_financials(pa)
    if fin is None:
        return None
    # annual_flat (GBT — one premium for the whole policy) and earnings_based
    # (WICA — a per-entity total rated on whole payroll) store a GROUP/policy
    # aggregate with no per-member reduction. Surfacing it as an individual's
    # premium overstates it by the cohort size, so drop it (keep the rate /
    # earnings, which are informational) rather than show a misleading figure.
    if fin.rate_basis in ("annual_flat", "earnings_based"):
        return fin.model_copy(update={"annual_premium": None, "num_employees": None})
    basis_amt = basis_amount(pa)
    if basis_amt is None:
        return fin
    bands = pa.get("voluntary_rates")
    if bands:
        # Voluntary life tier: rate depends on the member's age band. The band
        # table is already validated onto ``fin.voluntary_rates`` by
        # build_financials — carry it through so the UI can show it + a live
        # preview and the slip price tag can age-band off it.
        rate = voluntary_rate_for_age(bands, age)
        premium = round(basis_amt / 1000.0 * rate, 2) if rate is not None else None
        return fin.model_copy(
            update={
                "sum_insured": basis_amt,
                "premium_rate": rate,
                "annual_premium": premium,
                "num_employees": None,
            }
        )
    premium = None
    if fin.premium_rate is not None and fin.rate_basis == "per_1000_si":
        premium = round(basis_amt / 1000.0 * fin.premium_rate, 2)
    return fin.model_copy(
        update={
            "sum_insured": basis_amt,
            "annual_premium": premium,
            "num_employees": None,
        }
    )


def _scaled(value: float | None, m: float) -> float | None:
    return round(value * m, 2) if isinstance(value, (int, float)) else value


def apply_gst_to_financials(fin: PlanFinancials, multiplier: float) -> PlanFinancials:
    """Gross up a member's premium figures by the product's GST multiplier.

    Scales every PREMIUM-denominated field (annual premium, rates, the tiered /
    voluntary rate tables) and flags ``gst_included``. Coverage amounts
    (``sum_insured``, ``basis``, ``estimated_annual_earnings``) are not premiums
    and stay untouched. No-op for a 1.0 multiplier."""
    if multiplier == 1.0:
        return fin
    rate_tiers = None
    if fin.rate_tiers:
        rate_tiers = {
            label: {k: _scaled(v, multiplier) for k, v in cell.items()}
            for label, cell in fin.rate_tiers.items()
        }
    bands = None
    if fin.voluntary_rates:
        bands = [
            b.model_copy(update={"rate": _scaled(b.rate, multiplier)})
            for b in fin.voluntary_rates
        ]
    return fin.model_copy(
        update={
            "annual_premium": _scaled(fin.annual_premium, multiplier),
            "premium_rate": _scaled(fin.premium_rate, multiplier),
            "dependant_rate": _scaled(fin.dependant_rate, multiplier),
            "rate_tiers": rate_tiers or fin.rate_tiers,
            "voluntary_rates": bands or fin.voluntary_rates,
            "gst_included": True,
        }
    )
