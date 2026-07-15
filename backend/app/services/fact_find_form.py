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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Client,
    Dependant,
    Employee,
    Plan,
    PolicyYear,
    Product,
    ProductTerm,
)
from app.services.coverage_resolver import (
    batch_category_defaults,
    load_overrides,
    resolve_plan,
)
from app.services.roster_attributes import (
    DOB_KEYS,
    GENDER_KEYS,
    PASS_KEYS,
    REL_KEYS,
    age_next_birthday_as_of,
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

_SPOUSE_RE = re.compile(r"(?i)spouse|wife|husband|partner|married")
_CHILD_RE = re.compile(r"(?i)child|son|daughter|kid|dependent child")

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
    employees_count: int = 0
    dependants_count: int = 0
    # Members counted as Singaporean/PR only because their pass type was absent
    # (the local/foreign split was assumed, not derived) — surfaced in notes.
    defaulted_local_members: int = 0
    basis_rows: list[BasisRow] = field(default_factory=list)
    # age_bands[label] = (male, female); only the subset with DOB+gender
    age_bands: dict[str, tuple[int, int]] = field(default_factory=dict)
    # family[plan_name] = {"EO":n,"ES":n,"EC":n,"EF":n}
    family_local: dict[str, dict[str, int]] = field(default_factory=dict)
    family_foreign: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class FactFindContext:
    company_name: str
    total_employees: int
    insurer: str
    period_from: str
    period_to: str
    sections: dict[str, SectionContext]
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
    has_spouse = has_child = False
    for d in deps:
        rel = first_value(d.attribute_values or {}, REL_KEYS) or ""
        if _SPOUSE_RE.search(rel):
            has_spouse = True
        elif _CHILD_RE.search(rel):
            has_child = True
    if has_spouse and has_child:
        return "EF"
    if has_spouse:
        return "ES"
    if has_child:
        return "EC"
    return "EO"


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
    defaults: dict[str, dict[str, tuple[str, str | None]]]
    overrides: dict
    emp_cat_by_product: dict[str, dict[str, str]]


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
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(
                Product.id.in_({c.product_id for c in categories.values() if c.product_id})
            )
        ).scalars()
    }
    plans = {
        (p.product_id, (p.code or "")): p
        for p in db.execute(
            select(Plan).where(Plan.policy_year_id == policy_year.id)
        ).scalars()
    }
    terms = {
        t.product_id: t
        for t in db.execute(
            select(ProductTerm).where(ProductTerm.policy_year_id == policy_year.id)
        ).scalars()
    }
    # Which category each employee matched per product (for basis-of-cover rows).
    emp_cat_by_product: dict[str, dict[str, str]] = {}
    for emp in employees:
        for m in emp.matched_categories or []:
            cid = m.get("category_id")
            cat = categories.get(cid) if cid else None
            if cat and cat.product_id:
                emp_cat_by_product.setdefault(emp.id, {})[cat.product_id] = cid

    return _FormData(
        employees=employees,
        deps_by_emp=deps_by_emp,
        categories=categories,
        products=products,
        plans=plans,
        terms=terms,
        defaults=batch_category_defaults(db, employees),
        overrides=load_overrides(db, policy_year.id, [e.id for e in employees]),
        emp_cat_by_product=emp_cat_by_product,
    )


def _accumulate_basis(
    basis_acc: dict[tuple[str, str], dict],
    section_code: str,
    cid: str,
    cat: Category | None,
    plan: Plan | None,
    plan_name: str,
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
        }
        basis_acc[key] = acc
    acc["count"] += 1


def _aggregate(
    data: _FormData, ref: date
) -> tuple[dict[str, SectionContext], dict[tuple[str, str], dict], set[str]]:
    sections: dict[str, SectionContext] = {}
    basis_acc: dict[tuple[str, str], dict] = {}
    insurers: set[str] = set()

    def section(code: str) -> SectionContext:
        if code not in sections:
            sections[code] = SectionContext(code=code, title=SECTION_TITLES.get(code, code))
        return sections[code]

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
            sec.has_dependants = sec.has_dependants or bool(product and product.has_dependants)
            if product and product.insurer:
                sec.insurer = product.insurer
                insurers.add(product.insurer)
            term = data.terms.get(product_id)
            if term:
                sec.period_from = sec.period_from or _fmt(term.coverage_start)
                sec.period_to = sec.period_to or _fmt(term.coverage_end)

            cid = data.emp_cat_by_product.get(emp.id, {}).get(product_id)
            cat = data.categories.get(cid) if cid else None
            if cat and cat.participation_model:
                pm = cat.participation_model.lower()
                if sec.participation is None:
                    sec.participation = pm
                elif sec.participation != pm:
                    sec.participation = "mixed"

            covered_deps = deps if (product and product.has_dependants) else []
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

            if cid:
                _accumulate_basis(basis_acc, section_code, cid, cat, plan, plan_name)

    return sections, basis_acc, insurers


def build_context(db: Session, policy_year: PolicyYear) -> FactFindContext:
    client = db.get(Client, policy_year.client_id)
    data = _load_form_data(db, policy_year)
    ref = policy_year.start_date or date.today()
    sections, basis_acc, insurers = _aggregate(data, ref)

    # Collapse basis lines that would render identically. Combined sections
    # (GCGP + GCSP → one page) carry parallel categories per sub-product over the
    # same members, so the same designation/cover would otherwise appear twice;
    # keep one row with the larger headcount rather than double the rows.
    merged: dict[str, dict[tuple[str, str, str, str, str], dict]] = {}
    for (section_code, _cid), acc in basis_acc.items():
        if section_code not in sections:
            continue
        key = (
            acc["designation"], acc["plan_name"], acc["sum_insured"],
            acc["room_board"], acc["classification"],
        )
        bucket = merged.setdefault(section_code, {})
        prior = bucket.get(key)
        if prior is None or acc["count"] > prior["count"]:
            bucket[key] = acc
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
                )
            )
    for sec in sections.values():
        sec.basis_rows.sort(key=lambda r: r.num_employees, reverse=True)
        # Fall back to the policy-year term when a product has no ProductTerm.
        sec.period_from = sec.period_from or _fmt(policy_year.start_date)
        sec.period_to = sec.period_to or _fmt(policy_year.end_date)

    return FactFindContext(
        company_name=client.name if client else "",
        total_employees=len(data.employees),
        insurer=next(iter(sorted(insurers)), "") if len(insurers) == 1 else "",
        period_from=_fmt(policy_year.start_date),
        period_to=_fmt(policy_year.end_date),
        sections=sections,
        completeness=_completeness(sections, data.employees),
    )


# How many "Plan" rows each section's member-composition tables expose in the
# template — overflow beyond this is flagged rather than silently dropped.
_FAMILY_ROW_CAPACITY = {"GHS": 4, "GCM": 1, "GCGP_GCSP": 4}
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
            if sec.employees_count and band_total < sec.employees_count:
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
