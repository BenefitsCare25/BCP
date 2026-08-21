"""Auto-fill the Inspro Group Insurance Fact-Find Form from configured data.

The form (``app/templates/fact_find_form.docx``) is a Singapore group-insurance
quotation intake document designed to be *partially handwritten* ("complete in
block letters and ink"). This module fills every field the platform can resolve
**accurately** and leaves the rest blank for the broker, returning a
completeness report listing what was filled, partially filled, or skipped.

Two stages:

1. :func:`build_context` aggregates configured data into a presentation-shaped
   :class:`FactFindContext`. Effective per-employee coverage is resolved ONLY
   through ``coverage_resolver`` (category default + sparse override) so this
   never diverges from the benefit-statement / export read paths.
2. :func:`render_docx` loads the bundled template and writes the context into
   its table cells via label matching (robust to layout shifts), returning the
   filled ``.docx`` bytes.

Best-effort fields (age-band × gender, highest/oldest sum insured) depend on the
roster carrying date-of-birth and gender; when only a subset of members have
them, the table is filled for that subset and the gap is reported rather than
guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import (
    Category,
    Claim,
    Client,
    Dependant,
    Employee,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
)
from app.models.claim import CLAIM_STATUS_DRAFT, CLAIM_STATUS_PAID, CLAIM_STATUS_REJECTED
from app.services.cohort_tiers import cohort_key
from app.services.coverage_resolver import (
    load_overrides,
    resolve_plan,
)
from app.services.matching_engine import category_insured_entities
from app.services.plan_hydration import resolve_basis_amount
from app.services.product_insurer import insurer_map
from app.services.roster_attributes import (
    DOB_KEYS,
    GENDER_KEYS,
    PASS_KEYS,
    age_next_birthday_as_of,
    family_tier_bucket,
    first_value,
    parse_dob,
)

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "templates" / "fact_find_form.docx"

# Pass types that count as "Singaporeans & SPRs" on the GHS member split; the
# rest (EP/SP/WP holders) fall into the foreign-pass table.
_LOCAL_PASS = {"CITIZEN", "PR", "SC", "SPR", "SINGAPOREAN", "CITIZEN/PR"}

# Age bands on the form (Age Next Birthday). Upper bound inclusive; None = open.
AGE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("16 – 30", 16, 30),
    ("31 – 35", 31, 35),
    ("36 – 40", 36, 40),
    ("41 – 45", 41, 45),
    ("46 – 50", 46, 50),
    ("51 – 55", 51, 55),
    ("56 – 60", 56, 60),
    ("61 – 65", 61, 65),
    ("66 & above", 66, None),
)

# Inspro product code (or prefix) → canonical Fact-Find section code. Sections
# GTL/GPA/GHS/GCM/GCGP_GCSP/GBT have full pages in the template; the rest appear
# only on the General Information coverage matrix.
_SECTION_RULES: tuple[tuple[str, str], ...] = (
    ("GTL", "GTL"),
    ("GPA", "GPA"),
    ("GCM", "GCM"),  # before GHS — GCM is a GHS rider but its own section
    ("GMM", "GCM"),  # Group Major Medical = the catastrophic/major-medical rider
    ("GHS", "GHS"),
    ("GCGP", "GCGP_GCSP"),
    ("GCSP", "GCGP_GCSP"),
    ("GBT", "GBT"),
    ("GTPD", "GBT"),
    ("GDD", "GDD"),
    ("GCI", "GDD"),
    ("DENTAL", "GD"),
    ("GD", "GD"),
    ("MATERNITY", "GM"),
    ("GDI", "GDI"),
)

# Order products appear in the matrix / are rendered.
MATRIX_ORDER = ("GTL", "GPA", "GDD", "GHS", "GCM", "GCGP_GCSP", "GD", "GM", "GDI", "GBT")
# Sections that have a dedicated multi-page section in the template.
PAGE_SECTIONS = ("GTL", "GPA", "GHS", "GCM", "GCGP_GCSP", "GBT")
# Ceiling on basis-of-cover rows the renderer will clone into one table (runaway
# guard). Categories beyond this are dropped and flagged in the completeness notes.
MAX_BASIS_ROWS = 40

# Generic plan name the slip parser assigns when a product is a single benefit
# schedule with no named plan columns (e.g. GPA, where tiers differ by sum
# assured, not by named plans). Showing it in every basis row is noise — the
# sum-insured column already carries the differentiator — so it's blanked.
_PLACEHOLDER_PLAN_NAMES = frozenset({"schedule of benefits"})


def _plan_label(plan: Plan | None, fallback_code: str | None) -> str:
    """Display label for a resolved plan: the plan's name, unless it's the
    generic single-schedule placeholder (then blank), falling back to the code.
    """
    name = (plan.display_name if plan else "") or ""
    if name.strip().lower() in _PLACEHOLDER_PLAN_NAMES:
        return ""
    return name or (fallback_code or "")


def _natural_key(value: str) -> tuple[object, ...]:
    """Sort human plan labels as Plan 2 before Plan 10."""
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value or "")
    )


def section_for_code(code: str | None) -> str | None:
    """Map an Inspro product code to its Fact-Find section.

    An exact code match wins; otherwise the **longest** matching prefix wins, so
    a specific code (``GDI``) is never captured by a shorter one (``GD``)
    regardless of rule order.
    """
    if not code:
        return None
    up = code.strip().upper()
    best: tuple[int, str] | None = None
    for prefix, section in _SECTION_RULES:
        if up == prefix:
            return section
        if up.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), section)
    return best[1] if best else None


# ── Context dataclasses ──────────────────────────────────────────────────────
@dataclass
class BasisRow:
    designation: str
    num_employees: int
    plan_name: str = ""
    sum_insured: str = ""
    room_board: str = ""
    classification: str = ""
    max_limit: str = ""
    co_insurance: str = ""
    clinical_lines: list[BenefitLine] = field(default_factory=list)


@dataclass
class BenefitLine:
    name: str
    panel: str = ""
    max_limit: str = ""
    co_insurance: str = ""


@dataclass
class ClaimSummary:
    period: str
    employees: int
    claimants: int
    paid_count: int = 0
    paid_amount: float = 0
    outstanding_count: int = 0
    outstanding_amount: float = 0
    secondary_paid_amount: float = 0
    secondary_outstanding_amount: float = 0


@dataclass
class ClaimDetail:
    incurred_date: str
    nature: str
    paid_amount: float = 0
    outstanding_amount: float = 0


@dataclass
class OtherProductContext:
    code: str
    title: str
    employee_participation: str | None = None
    dependant_participation: str | None = None


@dataclass
class SectionContext:
    code: str
    title: str
    present: bool = True
    insurer: str = ""
    period_from: str = ""
    period_to: str = ""
    has_dependants: bool = False
    participation: str | None = None  # compulsory | voluntary | mixed
    employee_participation: str | None = None
    dependant_participation: str | None = None
    eligibility_date: str = ""
    free_cover_limit: str = ""
    nel_age_limit: str = ""
    employees_count: int = 0
    dependants_count: int = 0
    # Members counted as Singaporean/PR only because their pass type was absent
    # (the local/foreign split was assumed, not derived) — surfaced in notes.
    defaulted_local_members: int = 0
    available_plans: list[str] = field(default_factory=list)
    basis_rows: list[BasisRow] = field(default_factory=list)
    # age_bands[label] = (male, female); only the subset with DOB+gender
    age_bands: dict[str, tuple[int, int]] = field(default_factory=dict)
    # age_band_sums[label] = (male SI, female SI); only resolvable plain/salary bases
    age_band_sums: dict[str, tuple[float, float]] = field(default_factory=dict)
    highest_sum_insured_age: int | None = None
    highest_sum_insured: float | None = None
    oldest_insured_age: int | None = None
    oldest_insured_sum: float | None = None
    # family[plan_name] = {"EO":n,"ES":n,"EC":n,"EF":n}
    family_local: dict[str, dict[str, int]] = field(default_factory=dict)
    family_foreign: dict[str, dict[str, int]] = field(default_factory=dict)
    claim_summaries: list[ClaimSummary] = field(default_factory=list)
    claim_details: list[ClaimDetail] = field(default_factory=list)


@dataclass
class FactFindContext:
    company_name: str
    company_address: str
    nature_of_business: str
    country_of_origin: str
    total_employees: int
    insurer: str
    period_from: str
    period_to: str
    sections: dict[str, SectionContext]
    other_products: list[OtherProductContext]
    completeness: list[str]


# ── Helpers ──────────────────────────────────────────────────────────────────
# DOB parsing lives in roster_attributes (shared with the benefit statement +
# flex pricing) so the Excel midnight-tail handling can't drift between them.
_parse_dob = parse_dob


def _age_next_birthday(dob: date, ref: date) -> int:
    return age_next_birthday_as_of(dob, ref)


def _band_for(anb: int) -> str | None:
    for label, lo, hi in AGE_BANDS:
        if anb >= lo and (hi is None or anb <= hi):
            return label
    return None


def _gender_bucket(raw: str | None) -> str | None:
    if not raw:
        return None
    g = raw.strip().lower()
    if g in ("m", "male"):
        return "M"
    if g in ("f", "female"):
        return "F"
    return None


def _is_local_pass(raw: str | None) -> bool:
    """True for Singaporean/PR; default True when pass is unknown (most rosters)."""
    if not raw:
        return True
    return raw.strip().upper() in _LOCAL_PASS


def _family_bucket(deps: list[Dependant]) -> str:
    """Composite tier for this household — the shared implementation, so the
    fact-find's member tables and the slip export's count block can't disagree."""
    return family_tier_bucket(d.attribute_values or {} for d in deps)


_DESIG_DEP_TAIL_RE = re.compile(r"\s*/.*?(?:dependants?|dependents?).*$", re.IGNORECASE)
_DESIG_JOBCAT_RE = re.compile(r"\s*\([^)]*job\s*categ[^)]*\)", re.IGNORECASE)


def _clean_designation(name: str | None) -> str:
    """Clean a category name for the "Category of Employees / Designation" cell.

    Strips the internal ``(Job category: …)`` grade-code map and the
    ``/ … dependants`` basis tail, but KEEPS meaningful tier qualifiers like
    ``(Option 1)`` or ``(except for Director)`` — they distinguish designations.
    """
    if not name:
        return ""
    s = _DESIG_DEP_TAIL_RE.sub("", name)
    s = _DESIG_JOBCAT_RE.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _fmt_basis(val: object) -> str:
    """Render a plan_assignments 'basis' value for the quote form.

    Numeric sums lose their float artifact and get thousands separators
    (``250000.0`` → ``"250,000"``); textual bases ("12 x salary") pass through.
    """
    if val is None or isinstance(val, bool):
        return ""
    if isinstance(val, (int, float)):
        num = float(val)
    else:
        text = str(val).strip()
        if not text:
            return ""
        try:
            num = float(text.replace(",", ""))
        except ValueError:
            return text  # textual basis ("12 x salary") — pass through verbatim
    return f"{int(num):,}" if num.is_integer() else f"{num:,.2f}"


def _room_board(plan: Plan | None) -> str:
    if plan is None or not isinstance(plan.benefit_schedule, dict):
        return ""
    for item in plan.benefit_schedule.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if "room" in name and "board" in name:
            return str(item.get("value") or "").strip()
    return ""


def _plan_item(plan: Plan | None, *needles: str) -> dict[str, Any] | None:
    """Find a configured benefit row by tolerant name matching."""
    if plan is None or not isinstance(plan.benefit_schedule, dict):
        return None
    wanted = tuple(n.lower() for n in needles)
    for item in plan.benefit_schedule.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").lower()
        if any(n in name for n in wanted):
            return item
    return None


def _display_amount(value: object) -> str:
    """Human-readable amount/limit without inventing a currency."""
    return _fmt_basis(value)


def _item_property(item: dict[str, Any] | None, *keys: str) -> str:
    if not item:
        return ""
    props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
    for key in keys:
        value = props.get(key)
        if value not in (None, ""):
            return _display_amount(value)
    for limit in item.get("limits") or []:
        if not isinstance(limit, dict):
            continue
        label = str(limit.get("label") or "").lower()
        if any(key.replace("_", " ") in label for key in keys):
            return _display_amount(limit.get("value"))
    return ""


def _maximum_limit(plan: Plan | None) -> str:
    item = _plan_item(plan, "maximum benefit", "annual policy limit")
    if item and item.get("value") not in (None, ""):
        return _display_amount(item.get("value"))
    return _display_amount(plan.annual_policy_limit) if plan else ""


def _co_insurance(plan: Plan | None) -> str:
    item = _plan_item(plan, "daily room", "room & board", "room and board")
    raw = _item_property(item, "co_insurance")
    if not raw:
        return ""
    try:
        value = float(raw)
    except ValueError:
        return raw
    return f"{value * 100:g}%" if 0 <= value <= 1 else f"{value:g}%"


def _category_detail(cat: Category | None) -> dict[str, Any]:
    return cat.participation_detail if cat and isinstance(cat.participation_detail, dict) else {}


def _member_scope(cat: Category | None) -> str:
    pa = cat.plan_assignments if cat and isinstance(cat.plan_assignments, dict) else {}
    return str(pa.get("member_scope") or "employee").lower()


def _is_baseline_category(cat: Category) -> bool:
    """Alternative upgrade/downgrade and dependant rows are not default cover.

    They remain configured plan choices, but the fact-find basis must describe
    the cohort's baseline (plus any explicit employee override), not whichever
    sibling tier happened to win an overly-broad name match.
    """
    detail = _category_detail(cat)
    return _member_scope(cat) != "dependant" and not detail.get("direction")


def _baseline_category_map(categories: dict[str, Category]) -> dict[str, str]:
    """Map every employee-tier category to its cohort baseline category.

    Placement slips model optional upgrades/downgrades as sibling Category rows.
    A stale or overly-broad match can point at one of those siblings, but it is
    not the employee's default plan. Cohort-tier resolution defines the baseline
    as compulsory when available, otherwise the non-directional tier.
    """
    groups: dict[tuple[str, str, frozenset[str]], list[Category]] = {}
    for cat in categories.values():
        if not cat.product_id or _member_scope(cat) == "dependant":
            continue
        groups.setdefault(
            (
                cat.product_id,
                cohort_key(cat.raw_description or cat.display_name),
                category_insured_entities(cat),
            ),
            [],
        ).append(cat)
    out: dict[str, str] = {}
    for members in groups.values():
        baseline = next(
            (
                cat
                for cat in members
                if (_category_detail(cat).get("employee") or cat.participation_model)
                == "compulsory"
                and not _category_detail(cat).get("direction")
            ),
            None,
        )
        if baseline is None:
            baseline = next((cat for cat in members if _is_baseline_category(cat)), members[0])
        for cat in members:
            out[cat.id] = baseline.id
    return out


def _merge_mode(current: str | None, incoming: str | None) -> str | None:
    value = str(incoming or "").strip().lower() or None
    if value is None:
        return current
    if current is None or current == value:
        return value
    return "mixed"


def _setup_block(data: _FormData, product: Product | None, key: str) -> dict[str, Any]:
    if product is None:
        return {}
    answers = data.setups.get((product.code or "").strip().upper(), {})
    value = answers.get(key)
    return value if isinstance(value, dict) else {}


def _setup_text(data: _FormData, product: Product | None, block: str, key: str) -> str:
    return str(_setup_block(data, product, block).get(key) or "").strip()


def _configured_count(data: _FormData, product: Product, cat: Category) -> int:
    """Slip/setup-stated count for a category, including merged-cell rows.

    ProductSetup retains the parser's complete category list. It is the fallback
    when no live roster member matches a configured row; the materialized
    Category can legitimately have ``num_employees`` only on the first row of a
    visually merged count block.
    """
    answers = data.setups.get((product.code or "").upper(), {})
    pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
    wanted_name = re.sub(r"\s+", " ", cat.display_name or "").strip().lower()
    wanted_plan = str(pa.get("plan_code") or "")

    def as_count(value: object) -> int | None:
        if isinstance(value, bool) or value in (None, ""):
            return None
        try:
            return int(float(str(value).replace(",", "")))
        except ValueError:
            return None

    for row in answers.get("categories") or []:
        if not isinstance(row, dict):
            continue
        name = re.sub(r"\s+", " ", str(row.get("category") or "")).strip().lower()
        plan = str(row.get("plan_code") or "")
        if name == wanted_name and plan == wanted_plan:
            count = as_count(row.get("num_employees"))
            if count is not None and count > 0:
                return count
    return as_count(pa.get("num_employees")) or 0


def _configured_headcount(data: _FormData) -> int:
    """Most common complete configured product headcount for the year."""
    totals: list[int] = []
    for product in data.products.values():
        categories = [
            cat
            for cat in data.categories.values()
            if cat.product_id == product.id and cat.id in data.baseline_category_ids
        ]
        counts = [_configured_count(data, product, cat) for cat in categories]
        positive = [count for count in counts if count > 0]
        if positive and (len(positive) == len(categories) or len(positive) == 1):
            totals.append(sum(positive))
    if not totals:
        return 0
    return max(dict.fromkeys(totals), key=totals.count)


def _configured_section_headcount(data: _FormData, section_code: str) -> int:
    totals: list[int] = []
    for product in data.products.values():
        if section_for_code(product.code) != section_code:
            continue
        categories = [
            cat
            for cat in data.categories.values()
            if cat.product_id == product.id and cat.id in data.baseline_category_ids
        ]
        counts = [_configured_count(data, product, cat) for cat in categories]
        if any(counts):
            totals.append(sum(counts))
    return max(totals, default=0)


def _country_from_address(address: str) -> str:
    """Resolve country only when the configured address makes it unambiguous."""
    match = re.search(r"\b(Singapore)\s+\d{6}\s*$", address, re.IGNORECASE)
    return match.group(1).title() if match else ""


def _clinical_line(product_code: str, plan: Plan | None) -> list[BenefitLine]:
    if plan is None:
        return []
    code = product_code.upper()
    if code.startswith("GCGP"):
        item = _plan_item(plan, "panel")
        if item is None:
            return []
        return [
            BenefitLine(
                name="GPs",
                panel="Y",
                max_limit=_item_property(item, "per_visit", "per_policy_year"),
                co_insurance=_item_property(item, "co_payment"),
            )
        ]
    if code.startswith("GCSP"):
        lines: list[BenefitLine] = []
        for needle, label in (
            ("specialist care", "Specialist Care"),
            ("diagnostic x-ray", "Diagnostic X-ray & Lab Test"),
        ):
            item = _plan_item(plan, needle)
            if item is None:
                continue
            parts = []
            for sub in item.get("sub_items") or []:
                if not isinstance(sub, dict) or sub.get("value") in (None, ""):
                    continue
                name = str(sub.get("name") or "").lower()
                scope = "Panel" if "panel" in name and "non panel" not in name else "Non-panel"
                parts.append(f"{scope}: {_display_amount(sub.get('value'))}")
            value = "; ".join(parts) or _display_amount(item.get("value"))
            lines.append(BenefitLine(name=label, panel="Y / N", max_limit=value))
        return lines
    return []


# ── Context assembly ─────────────────────────────────────────────────────────
@dataclass
class _FormData:
    """Every row build_context needs, loaded in a handful of batched queries."""

    employees: list[Employee]
    deps_by_emp: dict[str, list[Dependant]]
    categories: dict[str, Category]
    products: dict[str, Product]
    plans: dict[tuple[str, str], Plan]
    terms: dict[str, ProductTerm]
    setups: dict[str, dict[str, Any]]
    # {product_id: insurer} for THIS year (services/product_insurer.py).
    insurers: dict[str, str]
    defaults: dict[str, dict[str, tuple[str, str | None]]]
    overrides: dict[str, Any]
    emp_cat_by_product: dict[str, dict[str, str]]
    baseline_category_ids: set[str]


def _load_form_data(db: Session, policy_year: PolicyYear) -> _FormData:
    employees = list(
        db.execute(
            select(Employee).where(
                Employee.policy_year_id == policy_year.id,
                Employee.status == "active",
            )
        ).scalars()
    )
    deps_by_emp: dict[str, list[Dependant]] = {}
    for dep in db.execute(
        select(Dependant).where(
            Dependant.policy_year_id == policy_year.id,
            Dependant.status == "active",
        )
    ).scalars():
        if dep.employee_id:
            deps_by_emp.setdefault(dep.employee_id, []).append(dep)

    categories = {
        c.id: c
        for c in db.execute(
            select(Category).where(Category.policy_year_id == policy_year.id)
        ).scalars()
    }
    plan_rows = list(
        db.execute(select(Plan).where(Plan.policy_year_id == policy_year.id)).scalars()
    )
    setup_rows = list(
        db.execute(
            select(ProductSetup).where(ProductSetup.policy_year_id == policy_year.id)
        ).scalars()
    )
    # A product is available when it is represented by a category, a plan, or
    # a completed setup. Restricting this query to Category rows made valid
    # plan-only products disappear from the generated form.
    product_ids = {
        product_id
        for product_id in (
            *(c.product_id for c in categories.values()),
            *(plan.product_id for plan in plan_rows),
            *(setup.materialized_product_id for setup in setup_rows),
        )
        if product_id
    }
    products = {
        p.id: p for p in db.execute(select(Product).where(Product.id.in_(product_ids))).scalars()
    }
    plans = {(p.product_id, (p.code or "")): p for p in plan_rows}
    terms = {
        t.product_id: t
        for t in db.execute(
            select(ProductTerm).where(ProductTerm.policy_year_id == policy_year.id)
        ).scalars()
    }
    setups = {(s.product_code or "").strip().upper(): (s.answers or {}) for s in setup_rows}
    # Which BASELINE category each employee matched per product. Optional
    # upgrade/downgrade siblings are plan choices, not default cover.
    baseline_map = _baseline_category_map(categories)
    emp_cat_by_product: dict[str, dict[str, str]] = {}
    defaults: dict[str, dict[str, tuple[str, str | None]]] = {
        employee.id: {} for employee in employees
    }
    for emp in employees:
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            cat = categories.get(cid) if cid else None
            if cat and cat.product_id:
                baseline_id = baseline_map.get(cat.id, cat.id)
                baseline = categories.get(baseline_id, cat)
                product = products.get(baseline.product_id or "")
                if product is None:
                    continue
                emp_cat_by_product.setdefault(emp.id, {})[product.id] = baseline.id
                pa = (
                    baseline.plan_assignments if isinstance(baseline.plan_assignments, dict) else {}
                )
                defaults[emp.id][product.id] = (
                    product.code,
                    str(pa.get("plan_code")) if pa.get("plan_code") not in (None, "") else None,
                )

    return _FormData(
        employees=employees,
        deps_by_emp=deps_by_emp,
        categories=categories,
        products=products,
        plans=plans,
        terms=terms,
        setups=setups,
        insurers=insurer_map(db, policy_year.id, products.values()),
        defaults=defaults,
        overrides=load_overrides(db, policy_year.id, [e.id for e in employees]),
        emp_cat_by_product=emp_cat_by_product,
        baseline_category_ids=set(baseline_map.values()),
    )


def _accumulate_basis(
    basis_acc: dict[tuple[str, str], dict[str, Any]],
    section_code: str,
    cid: str,
    cat: Category | None,
    plan: Plan | None,
    plan_name: str,
    product_code: str,
) -> None:
    """One basis-of-cover line per matched category (distinct cover line)."""
    key = (section_code, cid)
    acc = basis_acc.get(key)
    if acc is None:
        pa = cat.plan_assignments if (cat and isinstance(cat.plan_assignments, dict)) else {}
        acc = {
            "designation": _clean_designation(cat.display_name if cat else None),
            "count": 0,
            "plan_name": plan_name,
            # The textual "basis of cover" (e.g. "36 x salary", "$100,000");
            # never the aggregate sum_insured number.
            "sum_insured": _fmt_basis(pa.get("basis")),
            "room_board": _room_board(plan),
            "classification": str(pa.get("classification") or "").strip(),
            "max_limit": _maximum_limit(plan),
            "co_insurance": _co_insurance(plan),
            "clinical_lines": _clinical_line(product_code, plan),
        }
        basis_acc[key] = acc
    acc["count"] += 1


def _aggregate(
    data: _FormData, ref: date
) -> tuple[dict[str, SectionContext], dict[tuple[str, str], dict[str, Any]], set[str]]:
    sections: dict[str, SectionContext] = {}
    basis_acc: dict[tuple[str, str], dict[str, Any]] = {}
    exact_configured_counts: dict[str, int] = {}
    insurers: set[str] = set()

    def section(code: str) -> SectionContext:
        if code not in sections:
            sections[code] = SectionContext(code=code, title=SECTION_TITLES.get(code, code))
        return sections[code]

    # Seed every CONFIGURED product before looking at the roster. Previously a
    # product disappeared from the form when no member matched it (CDL's GBT),
    # even though its plans, insurer and eligibility were fully configured.
    for product in sorted(data.products.values(), key=lambda value: value.code):
        section_code = section_for_code(product.code)
        if section_code is None:
            continue
        sec = section(section_code)
        product_insurer = data.insurers.get(product.id, "")
        if product_insurer:
            current = {value.strip() for value in sec.insurer.split(" / ") if value.strip()}
            current.add(product_insurer)
            sec.insurer = " / ".join(sorted(current))
            insurers.add(product_insurer)
        term = data.terms.get(product.id)
        if term:
            sec.period_from = sec.period_from or _fmt(term.coverage_start)
            sec.period_to = sec.period_to or _fmt(term.coverage_end)
            sec.free_cover_limit = sec.free_cover_limit or _display_amount(term.free_cover_limit)
            sec.nel_age_limit = sec.nel_age_limit or (
                str(term.nel_age_limit) if term.nel_age_limit is not None else ""
            )
        sec.eligibility_date = sec.eligibility_date or _setup_text(
            data, product, "eligibility", "eligibility_date"
        )

        product_plans = sorted(
            (plan for (product_id, _), plan in data.plans.items() if product_id == product.id),
            key=lambda value: _natural_key(value.display_name or value.code),
        )
        for plan in product_plans:
            label = _plan_label(plan, plan.code)
            if label and label not in sec.available_plans:
                sec.available_plans.append(label)

        product_categories = [
            cat for cat in data.categories.values() if cat.product_id == product.id
        ]
        baseline_categories = [
            cat for cat in product_categories if cat.id in data.baseline_category_ids
        ]
        configured_counts = {
            cat.id: _configured_count(data, product, cat) for cat in baseline_categories
        }
        positive_counts = {
            category_id: count for category_id, count in configured_counts.items() if count > 0
        }
        # Per-category setup counts are authoritative when every row has one.
        # A single non-zero value across several categories is a merged grand
        # total, so retain the roster-derived distribution instead.
        if len(positive_counts) == len(baseline_categories):
            exact_configured_counts.update(positive_counts)
        for cat in baseline_categories:
            detail = _category_detail(cat)
            sec.employee_participation = _merge_mode(
                sec.employee_participation,
                detail.get("employee") or cat.participation_model,
            )
            sec.dependant_participation = _merge_mode(
                sec.dependant_participation, detail.get("dependant")
            )
            pa = cat.plan_assignments if isinstance(cat.plan_assignments, dict) else {}
            plan_code = str(pa.get("plan_code") or "")
            plan = data.plans.get((product.id, plan_code))
            _accumulate_basis(
                basis_acc,
                section_code,
                cat.id,
                cat,
                plan,
                _plan_label(plan, plan_code),
                product.code,
            )
            # Seeding calls the same accumulator as live membership; cancel its
            # increment so the row starts at zero and live members add normally.
            basis_acc[(section_code, cat.id)]["count"] = 0

        # A plan-only product is still configured and must not vanish from the
        # form merely because its category mapping has not been materialized.
        # In that case render the available plan names with blank designation
        # and cover fields instead of fabricating those missing relationships.
        if not baseline_categories:
            for plan in product_plans:
                _accumulate_basis(
                    basis_acc,
                    section_code,
                    f"plan:{product.id}:{plan.code}",
                    None,
                    plan,
                    _plan_label(plan, plan.code),
                    product.code,
                )
                basis_acc[(section_code, f"plan:{product.id}:{plan.code}")]["count"] = 0

        dependant_categories = [
            cat for cat in product_categories if _member_scope(cat) == "dependant"
        ]
        for cat in dependant_categories:
            detail = _category_detail(cat)
            sec.dependant_participation = _merge_mode(
                sec.dependant_participation,
                detail.get("dependant") or detail.get("employee") or cat.participation_model,
            )
        sec.has_dependants = bool(
            product.has_dependants or dependant_categories or sec.dependant_participation
        )
        sec.participation = sec.employee_participation

    for emp in data.employees:
        av = emp.attribute_values or {}
        dob = _parse_dob(first_value(av, DOB_KEYS))
        gender = _gender_bucket(first_value(av, GENDER_KEYS))
        pass_raw = first_value(av, PASS_KEYS)
        local = _is_local_pass(pass_raw)
        deps = data.deps_by_emp.get(emp.id, [])
        anb_band = _band_for(_age_next_birthday(dob, ref)) if dob else None
        # Count each employee once per Fact-Find section even when several
        # underlying products map to it — the form has one page per section.
        counted: set[str] = set()

        for product_id, (product_code, default_plan) in data.defaults.get(emp.id, {}).items():
            section_code = section_for_code(product_code)
            if section_code is None:
                continue
            override = data.overrides.get((emp.id, product_id))
            resolved = resolve_plan(override, default_plan)
            if resolved.declined:
                continue  # declined override drops the coverage line entirely

            product = data.products.get(product_id)
            sec = section(section_code)

            cid = data.emp_cat_by_product.get(emp.id, {}).get(product_id)
            cat = data.categories.get(cid) if cid else None
            # An explicit tier election carries the actual category/basis. A
            # plan-code-only override keeps the baseline category.
            if override and override.tier_category_id:
                cat = data.categories.get(override.tier_category_id, cat)
                cid = cat.id if cat else cid

            covered_deps = deps if sec.has_dependants else []
            if override and override.covered_dependant_ids is not None:
                ids = set(override.covered_dependant_ids)
                covered_deps = [d for d in deps if d.id in ids]

            plan = data.plans.get((product_id, resolved.plan_code or ""))
            plan_name = _plan_label(plan, resolved.plan_code)

            if section_code not in counted:
                counted.add(section_code)
                sec.employees_count += 1
                sec.dependants_count += len(covered_deps)
                if local and pass_raw is None:
                    sec.defaulted_local_members += 1
                fam_bucket = _family_bucket(covered_deps) if covered_deps else "EO"
                fam_map = sec.family_local if local else sec.family_foreign
                row = fam_map.setdefault(plan_name or "Plan", {"EO": 0, "ES": 0, "EC": 0, "EF": 0})
                row[fam_bucket] += 1
                if anb_band and gender:
                    m, f = sec.age_bands.get(anb_band, (0, 0))
                    sec.age_bands[anb_band] = (m + (gender == "M"), f + (gender == "F"))

                    plan_assignments = (
                        cat.plan_assignments
                        if cat and isinstance(cat.plan_assignments, dict)
                        else {}
                    )
                    basis = resolve_basis_amount(plan_assignments, av)
                    if basis is not None:
                        male_sum, female_sum = sec.age_band_sums.get(anb_band, (0.0, 0.0))
                        sec.age_band_sums[anb_band] = (
                            male_sum + (basis if gender == "M" else 0),
                            female_sum + (basis if gender == "F" else 0),
                        )
                        age = _age_next_birthday(dob, ref) if dob else None
                        if sec.highest_sum_insured is None or basis > sec.highest_sum_insured:
                            sec.highest_sum_insured = basis
                            sec.highest_sum_insured_age = age
                        if age is not None and (
                            sec.oldest_insured_age is None or age > sec.oldest_insured_age
                        ):
                            sec.oldest_insured_age = age
                            sec.oldest_insured_sum = basis

            if cid and product:
                key = (section_code, cid)
                if key not in basis_acc:
                    _accumulate_basis(
                        basis_acc,
                        section_code,
                        cid,
                        cat,
                        plan,
                        plan_name,
                        product.code,
                    )
                else:
                    basis_acc[key]["count"] += 1

    # A configured category with no live match still belongs in the form. Use
    # its setup/slip-stated figure as one coherent fallback (never silently drop
    # the plan row merely because matching is incomplete).
    for (_section_code, cid), acc in basis_acc.items():
        if cid in exact_configured_counts:
            acc["count"] = exact_configured_counts[cid]
            continue
        if acc["count"]:
            continue
        cat = data.categories.get(cid)
        product = data.products.get(cat.product_id or "") if cat else None
        if cat and product:
            acc["count"] = _configured_count(data, product, cat)

    return sections, basis_acc, insurers


def _common_setup_value(data: _FormData, block: str, key: str) -> str:
    """Most common non-blank configured value across this year's products."""
    counts: dict[str, int] = {}
    order: list[str] = []
    for code in sorted(data.setups):
        value = data.setups[code].get(block)
        raw = value.get(key) if isinstance(value, dict) else None
        text = str(raw or "").strip()
        if not text:
            continue
        if text not in counts:
            order.append(text)
        counts[text] = counts.get(text, 0) + 1
    if not counts:
        return ""
    return max(order, key=lambda value: counts[value])


def _other_product_contexts(data: _FormData) -> list[OtherProductContext]:
    out: list[OtherProductContext] = []
    for product in sorted(data.products.values(), key=lambda value: value.code):
        if section_for_code(product.code) is not None:
            continue
        employee_mode: str | None = None
        dependant_mode: str | None = None
        for cat in data.categories.values():
            if cat.product_id != product.id:
                continue
            detail = _category_detail(cat)
            if _member_scope(cat) == "dependant":
                dependant_mode = _merge_mode(
                    dependant_mode,
                    detail.get("dependant") or detail.get("employee") or cat.participation_model,
                )
            elif _is_baseline_category(cat):
                employee_mode = _merge_mode(
                    employee_mode, detail.get("employee") or cat.participation_model
                )
                dependant_mode = _merge_mode(dependant_mode, detail.get("dependant"))
        out.append(
            OtherProductContext(
                code=product.code,
                title=product.display_name,
                employee_participation=employee_mode,
                dependant_participation=dependant_mode,
            )
        )
    return out


def _claim_amount(claim: Any) -> float:
    if claim.payment_amount is not None:
        return float(claim.payment_amount)
    if claim.amount_approved is not None:
        return float(claim.amount_approved)
    if claim.currency == "SGD":
        return float(claim.amount_claimed)
    return float(claim.amount_converted) if claim.amount_converted is not None else 0.0


def _populate_claims(
    db: Session,
    policy_year: PolicyYear,
    sections: dict[str, SectionContext],
) -> None:
    """Populate the form's three-year claim summaries from platform claims."""
    years = list(
        db.execute(
            select(PolicyYear)
            .where(
                PolicyYear.client_id == policy_year.client_id,
                PolicyYear.end_date <= policy_year.end_date,
            )
            .order_by(PolicyYear.end_date.desc())
            .limit(3)
        ).scalars()
    )
    if not years:
        return
    year_by_id = {year.id: year for year in years}
    employee_ids_by_year: dict[str, set[str]] = {year.id: set() for year in years}
    for year_id, employee_id in db.execute(
        select(Employee.policy_year_id, Employee.id).where(
            Employee.policy_year_id.in_(year_by_id), Employee.status == "active"
        )
    ).all():
        employee_ids_by_year[year_id].add(employee_id)

    claims = list(
        db.execute(
            select(
                Claim.policy_year_id,
                Claim.employee_id,
                Claim.product_code,
                Claim.status,
                Claim.incurred_date,
                Claim.sub_type,
                Claim.claim_type,
                Claim.benefit_key,
                Claim.payment_amount,
                Claim.amount_approved,
                Claim.currency,
                Claim.amount_claimed,
                Claim.amount_converted,
            ).where(
                Claim.policy_year_id.in_(year_by_id),
                Claim.status.notin_((CLAIM_STATUS_DRAFT, CLAIM_STATUS_REJECTED)),
                Claim.claim_kind == "insured",
            )
        ).all()
    )
    grouped: dict[tuple[str, str], list[Claim]] = {}
    for claim in claims:
        code = section_for_code(claim.product_code)
        if code is None or code not in sections:
            continue
        grouped.setdefault((code, claim.policy_year_id), []).append(claim)

    for code, sec in sections.items():
        for year in sorted(years, key=lambda value: value.end_date):
            rows = grouped.get((code, year.id), [])
            if not rows:
                continue
            paid = [claim for claim in rows if claim.status == CLAIM_STATUS_PAID]
            outstanding = [claim for claim in rows if claim.status != CLAIM_STATUS_PAID]
            primary = [
                claim for claim in rows if not (claim.product_code or "").upper().startswith("GCSP")
            ]
            secondary = [claim for claim in rows if claim not in primary]
            primary_paid = [claim for claim in primary if claim.status == CLAIM_STATUS_PAID]
            primary_outstanding = [claim for claim in primary if claim.status != CLAIM_STATUS_PAID]
            secondary_paid = [claim for claim in secondary if claim.status == CLAIM_STATUS_PAID]
            secondary_outstanding = [
                claim for claim in secondary if claim.status != CLAIM_STATUS_PAID
            ]
            sec.claim_summaries.append(
                ClaimSummary(
                    period=f"{_fmt(year.start_date)} to {_fmt(year.end_date)}",
                    employees=len(employee_ids_by_year.get(year.id, set())),
                    claimants=len({claim.employee_id for claim in rows if claim.employee_id}),
                    paid_count=len(paid),
                    paid_amount=sum(_claim_amount(claim) for claim in primary_paid),
                    outstanding_count=len(outstanding),
                    outstanding_amount=sum(_claim_amount(claim) for claim in primary_outstanding),
                    secondary_paid_amount=sum(_claim_amount(claim) for claim in secondary_paid),
                    secondary_outstanding_amount=sum(
                        _claim_amount(claim) for claim in secondary_outstanding
                    ),
                )
            )
        current_rows = grouped.get((code, policy_year.id), [])
        for claim in sorted(current_rows, key=lambda value: value.incurred_date, reverse=True):
            amount = _claim_amount(claim)
            sec.claim_details.append(
                ClaimDetail(
                    incurred_date=_fmt(claim.incurred_date),
                    nature=claim.sub_type or claim.claim_type or claim.benefit_key or "Claim",
                    paid_amount=amount if claim.status == CLAIM_STATUS_PAID else 0,
                    outstanding_amount=amount if claim.status != CLAIM_STATUS_PAID else 0,
                )
            )


def build_context(db: Session, policy_year: PolicyYear) -> FactFindContext:
    client = db.get(Client, policy_year.client_id)
    data = _load_form_data(db, policy_year)
    ref = policy_year.start_date or business_today()
    sections, basis_acc, insurers = _aggregate(data, ref)

    # Collapse basis lines that would render identically. Combined sections
    # (GCGP + GCSP → one page) carry parallel categories per sub-product over the
    # same members, so the same designation/cover would otherwise appear twice;
    # keep one row with the larger headcount rather than double the rows.
    merged: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
    for (section_code, _cid), acc in basis_acc.items():
        if section_code not in sections:
            continue
        key = (
            acc["designation"],
            acc["plan_name"],
            acc["sum_insured"],
            acc["room_board"],
            acc["classification"],
            # GP and specialist products share one physical plan row whose
            # benefit-specific limits live on its three child rows. Do not
            # split that plan merely because those child limits differ.
            "" if section_code == "GCGP_GCSP" else acc["max_limit"],
            "" if section_code == "GCGP_GCSP" else acc["co_insurance"],
        )
        bucket = merged.setdefault(section_code, {})
        prior = bucket.get(key)
        if prior is None:
            bucket[key] = acc
        else:
            known = {
                (line.name, line.panel, line.max_limit, line.co_insurance)
                for line in prior["clinical_lines"]
            }
            prior["clinical_lines"].extend(
                line
                for line in acc["clinical_lines"]
                if (line.name, line.panel, line.max_limit, line.co_insurance) not in known
            )
            prior["count"] = max(prior["count"], acc["count"])
    for section_code, bucket in merged.items():
        sec = sections[section_code]
        for acc in bucket.values():
            sec.basis_rows.append(
                BasisRow(
                    designation=acc["designation"],
                    num_employees=acc["count"],
                    plan_name=acc["plan_name"],
                    sum_insured=acc["sum_insured"],
                    room_board=acc["room_board"],
                    classification=acc["classification"],
                    max_limit=acc["max_limit"],
                    co_insurance=acc["co_insurance"],
                    clinical_lines=acc["clinical_lines"],
                )
            )
    configured_headcount = _configured_headcount(data) or len(data.employees)
    for sec in sections.values():
        sec.basis_rows.sort(key=lambda r: r.num_employees, reverse=True)
        configured_members = _configured_section_headcount(data, sec.code)
        if configured_members:
            sec.employees_count = configured_members
        elif sec.employees_count == 0 and any(
            "all other employees" in row.designation.lower() for row in sec.basis_rows
        ):
            # An exhaustive catch-all category establishes company-wide cover
            # even when this product has no roster matching rule (as with GBT).
            sec.employees_count = configured_headcount
        # Fall back to the policy-year term when a product has no ProductTerm.
        sec.period_from = sec.period_from or _fmt(policy_year.start_date)
        sec.period_to = sec.period_to or _fmt(policy_year.end_date)

    _populate_claims(db, policy_year, sections)

    insured_name = _common_setup_value(data, "header", "insured")
    policyholder = _common_setup_value(data, "header", "policyholder")
    company_name = insured_name or policyholder or (client.legal_name if client else "")
    if not company_name and client:
        company_name = client.name
    other_products = _other_product_contexts(data)

    completeness = _completeness(sections, data.employees)
    if other_products:
        labels = ", ".join(product.code for product in other_products)
        completeness.append(
            f"{labels}: listed under Other products in General Information; "
            "the standard Fact-Find template has no dedicated product page(s)."
        )

    company_address = _common_setup_value(data, "header", "address")
    return FactFindContext(
        company_name=company_name,
        company_address=company_address,
        nature_of_business=_common_setup_value(data, "header", "business"),
        country_of_origin=_country_from_address(company_address),
        total_employees=configured_headcount,
        insurer=next(iter(sorted(insurers)), "") if len(insurers) == 1 else "",
        period_from=_fmt(policy_year.start_date),
        period_to=_fmt(policy_year.end_date),
        sections=sections,
        other_products=other_products,
        completeness=completeness,
    )


# How many "Plan" rows each section's member-composition tables expose in the
# template — overflow beyond this is flagged rather than silently dropped.
_FAMILY_ROW_CAPACITY = {"GHS": MAX_BASIS_ROWS, "GCM": MAX_BASIS_ROWS, "GCGP_GCSP": MAX_BASIS_ROWS}
# Sections whose member tables split Singaporean/PR vs EP/SP/WP holders.
_PASS_SPLIT_SECTIONS = frozenset({"GHS", "GCM"})


def _completeness(sections: dict[str, SectionContext], employees: list[Employee]) -> list[str]:
    notes: list[str] = []
    if not employees:
        notes.append("No active employees on this policy year — member tables left blank.")
    for code in MATRIX_ORDER:
        sec = sections.get(code)
        if not sec:
            continue
        title = SECTION_TITLES.get(code, code)
        if code in PAGE_SECTIONS:
            band_total = sum(m + f for m, f in sec.age_bands.values())
            if code != "GBT" and sec.employees_count and band_total < sec.employees_count:
                notes.append(
                    f"{title}: age/gender table filled for {band_total} of "
                    f"{sec.employees_count} members (roster missing DOB/gender for the rest)."
                )
            if len(sec.basis_rows) > MAX_BASIS_ROWS:
                dropped = len(sec.basis_rows) - MAX_BASIS_ROWS
                notes.append(
                    f"{title}: {len(sec.basis_rows)} basis-of-cover categories exceed the "
                    f"{MAX_BASIS_ROWS}-row form cap — the {dropped} smallest were omitted; "
                    "complete them manually."
                )
            cap = _FAMILY_ROW_CAPACITY.get(code)
            if cap is not None:
                fam_tables = (
                    ("Singaporean/PR", sec.family_local),
                    ("EP/SP/WP", sec.family_foreign),
                )
                for label, fam in fam_tables:
                    if len(fam) > cap:
                        notes.append(
                            f"{title}: {len(fam)} {label} plans but the form has {cap} "
                            f"member-table row(s) — only the largest {cap} shown."
                        )
            # The Singaporean/PR vs EP/SP/WP split (GHS/GCM) needs a pass type;
            # members without one were assumed local — flag so it's verified.
            if code in _PASS_SPLIT_SECTIONS and sec.defaulted_local_members:
                notes.append(
                    f"{title}: {sec.defaulted_local_members} member(s) had no pass type "
                    "and were counted as Singaporean/PR — verify the EP/SP/WP split."
                )
            if code in _FAMILY_ROW_CAPACITY:
                family_total = sum(
                    sum(counts.values())
                    for family in (sec.family_local, sec.family_foreign)
                    for counts in family.values()
                )
                if family_total and family_total < sec.employees_count:
                    notes.append(
                        f"{title}: member-composition tables contain {family_total} of "
                        f"{sec.employees_count} configured members; verify the remaining "
                        f"{sec.employees_count - family_total}."
                    )
        if code == "GBT" and sec.basis_rows:
            notes.append(
                f"{title}: travel frequency, trip count, duration, destination area, "
                "and leisure-only indicators are not stored in the platform; those "
                "tables were left blank rather than inferred."
            )
    return notes


def _fmt(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


SECTION_TITLES = {
    "GTL": "Group Term Life (GTL)",
    "GPA": "Group Personal Accident (GPA)",
    "GDD": "Group Dread Disease (GDD)",
    "GHS": "Group Hospital & Surgical (GHS)",
    "GCM": "Group Catastrophic Medical (GCM)",
    "GCGP_GCSP": "Group Clinical GP & SP (GCGP & GCSP)",
    "GD": "Group Dental (GD)",
    "GM": "Group Maternity (GM)",
    "GDI": "Group Disability Income (GDI)",
    "GBT": "Group Business Travel (GBT)",
}


# Rendering lives in fact_find_render.py, which imports from this module — the
# dependency is one-directional (render → form). The orchestrator that ties
# build_context to render_docx is `fact_find_render.generate`.
__all__ = [
    "TEMPLATE_PATH",
    "BasisRow",
    "FactFindContext",
    "SectionContext",
    "build_context",
    "section_for_code",
]
