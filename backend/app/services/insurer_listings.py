"""Per-insurer employee + dependant listing reports.

Column layout mirrors the insurer billing templates: a shared demographic
block, then per-product blocks grouped by the insurer assigned to each product
(``Product.insurer``):

- lump-sum products (categories carry a numeric per-member basis — GTL/GCI/
  GPA):  Basis of Cover / Eligible SI / SI Pending U/W / Last Accepted SI,
  with dependant SP/CH role blocks resolved from dependant-scope categories;
- schedule products (GHS/GMM/outpatient/dental): Plan/Basis of Cover (the
  plan's ``report_label``) + Family Grouping (EO/ES/EC/EF from the covered
  dependants).

Underwriting amounts come from ``services.underwriting`` (no case → fully
accepted). Report codes come from ``product_metadata.report_code``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Category,
    Dependant,
    Employee,
    Plan,
    PolicyYear,
    Product,
)
from app.models.dependant import (
    DEPENDANT_STATUS_ACTIVE,
    DEPENDANT_STATUS_TERMINATED,
)
from app.services.benefit_statement import _category_covers_dependants
from app.services.coverage_resolver import load_overrides
from app.services.flex_membership import classify_relationship
from app.services.insurer_reports import (
    _as_date,
    _autosize,
    _bold_header,
    _last_day_of_service,
    append_safe,
    report_employees,
)
from app.services.leave_pricing_resolver import leave_sell_eligible
from app.services.plan_hydration import basis_amount, hydrate_plans
from app.services.roster_attributes import (
    DEPENDANT_ID_KEYS,
    EMPLOYEE_ID_KEYS,
    REL_KEYS,
    first_value,
    mask_nric,
    nric_from_attrs,
)
from app.services.roster_parser import INSURER_MEMBER_ID_KEY
from app.services.underwriting import (
    free_cover_limits,
    load_cases,
    report_uw_amounts,
)

_OPTION_MARKER = re.compile(r"\(\s*option\s*(\d+)\s*\)", re.IGNORECASE)

_ROLE_CODES = {"spouse": "SP", "child": "CH"}


@dataclass
class DepOption:
    category_id: str
    basis: float | None
    marker: str | None  # "(Option N)" ordinal as a string, e.g. "2"


@dataclass
class ProductBlock:
    product: Product
    report_code: str
    lump_sum: bool
    plans: dict[str, Plan] = field(default_factory=dict)  # by plan code
    role_options: dict[str, list[DepOption]] = field(default_factory=dict)


@dataclass
class EmployeeCoverage:
    plan_code: str | None = None
    plan_label: str | None = None
    basis_display: str | None = None
    eligible: float | None = None
    covered_dependant_ids: list[str] = field(default_factory=list)
    grouping: str = "EO"
    option_marker: str | None = None
    dependant_option_ids: dict | None = None


def _dep_reportable(dep: Dependant, start: date | None) -> bool:
    """A dependant belongs on an insurer listing when active, or terminated
    within the policy period — mirrors ``report_employees`` for leavers so a
    dependant who left mid-year still appears (with a termination date) for the
    insurer to off-bill. Pre-period terminations are excluded."""
    if dep.status == DEPENDANT_STATUS_ACTIVE:
        return True
    if dep.status != DEPENDANT_STATUS_TERMINATED:
        return False
    if dep.terminated_effective is None or start is None:
        return True
    return dep.terminated_effective >= start


def _report_code(p: Product) -> str:
    meta = p.product_metadata or {}
    return str(meta.get("report_code") or p.code)


def _money(amount: float) -> str:
    return f"S$ {amount:,.0f}"


def product_blocks(db: Session, py: PolicyYear) -> list[ProductBlock]:
    """One block per product with categories in the year, column-ordered:
    lump-sum products first (matching the insurer templates), then schedule
    products, each by report code."""
    cats = list(
        db.execute(
            select(Category).where(Category.policy_year_id == py.id)
        ).scalars().all()
    )
    product_ids = {c.product_id for c in cats if c.product_id}
    if not product_ids:
        return []
    products = {
        p.id: p
        for p in db.execute(
            select(Product).where(Product.id.in_(product_ids))
        ).scalars().all()
    }
    plans = list(
        db.execute(
            select(Plan).where(Plan.policy_year_id == py.id)
        ).scalars().all()
    )

    blocks: dict[str, ProductBlock] = {}
    for pid, product in products.items():
        blocks[pid] = ProductBlock(
            product=product, report_code=_report_code(product), lump_sum=False
        )
    for plan in plans:
        if plan.product_id in blocks:
            blocks[plan.product_id].plans[plan.code] = plan

    for cat in cats:
        if not cat.product_id or cat.product_id not in blocks:
            continue
        block = blocks[cat.product_id]
        pa = cat.plan_assignments or {}
        if pa.get("member_scope") == "dependant":
            text = f"{cat.display_name or ''} {cat.raw_description or ''}"
            role = classify_relationship(text)
            if role:
                m = _OPTION_MARKER.search(text)
                block.role_options.setdefault(role, []).append(DepOption(
                    category_id=cat.id,
                    basis=basis_amount(pa),
                    marker=m.group(1) if m else None,
                ))
        elif pa.get("basis") not in (None, ""):
            # A per-member ``basis`` (sum-assured) marks a lump-sum product —
            # GTL/GCI/GPA. Classify on the presence of the basis, NOT on
            # basis_amount() being numeric: a salary-multiple basis ("36 times
            # basic monthly salary") is non-numeric yet still a life/lump-sum
            # product and must get the SI/Pending-U/W columns, not the
            # schedule-plan columns. (Its per-member SI shows as the basis
            # expression with blank numeric SI until salary-resolved.)
            block.lump_sum = True

    return sorted(
        blocks.values(), key=lambda b: (not b.lump_sum, b.report_code)
    )


def _employee_coverage(
    db: Session,
    py: PolicyYear,
    employees: list[Employee],
    blocks: list[ProductBlock],
) -> tuple[dict[str, dict[str, EmployeeCoverage]], dict[str, list[Dependant]]]:
    """({employee_id: {product_id: EmployeeCoverage}}, {employee_id: [deps]})."""
    plans_by_emp = hydrate_plans(employees, db, py.id)
    overrides = load_overrides(db, py.id, [e.id for e in employees])
    block_by_code = {b.product.code: b for b in blocks}

    cat_rows = db.execute(
        select(
            Category.id, Category.plan_assignments,
            Category.display_name, Category.raw_description,
        ).where(Category.policy_year_id == py.id)
    ).all()
    cat_facts = {cid: (pa or {}, disp, raw) for cid, pa, disp, raw in cat_rows}

    deps_by_emp: dict[str, list[Dependant]] = {}
    for dep in db.execute(
        select(Dependant).where(
            Dependant.policy_year_id == py.id,
            Dependant.status.in_(
                [DEPENDANT_STATUS_ACTIVE, DEPENDANT_STATUS_TERMINATED]
            ),
        )
    ).scalars().all():
        if dep.employee_id:
            deps_by_emp.setdefault(dep.employee_id, []).append(dep)

    start = py.start_date
    coverage: dict[str, dict[str, EmployeeCoverage]] = {}
    for emp in employees:
        # Active dependants plus in-period leavers — a dependant who terminated
        # mid-year was covered for part of the period and must appear on the
        # listing (with a termination date), like an employee leaver.
        report_deps = [
            d for d in deps_by_emp.get(emp.id, []) if _dep_reportable(d, start)
        ]
        dep_role = {
            d.id: classify_relationship(first_value(d.attribute_values or {}, REL_KEYS))
            for d in report_deps
        }
        per_product: dict[str, EmployeeCoverage] = {}
        for mp in plans_by_emp.get(emp.id, []):
            block = block_by_code.get(mp.product_code)
            if block is None or block.product.id in per_product:
                continue
            pa, disp, raw = cat_facts.get(mp.category_id or "", ({}, None, None))
            eligible = basis_amount(pa)
            basis_raw = pa.get("basis")
            if mp.covered_dependant_ids is not None:
                covered = [
                    d.id for d in report_deps if d.id in mp.covered_dependant_ids
                ]
            else:
                # Dependant-scope option categories (GPA/GTL/GCI Spouse/Child
                # levels) prove dependant cover even when the catalog's
                # has_dependants flag says otherwise.
                covers = bool(block.role_options) or _category_covers_dependants(
                    bool(block.product.has_dependants), pa, disp, raw
                )
                covered = [d.id for d in report_deps] if covers else []
            # Family grouping (EO/ES/EC/EF): any covered dependant lifts it off
            # employee-only. A spouse gives ES, a non-spouse dependant (child or
            # an unclassified relationship — billed on the with-dependants side)
            # gives EC, both give EF. Never report EO while a dependant is
            # covered, so the employee row can't contradict the dependant listing.
            covered_set = set(covered)
            covered_roles = [dep_role[d.id] for d in report_deps if d.id in covered_set]
            has_spouse = any(r == "spouse" for r in covered_roles)
            has_other = any(r != "spouse" for r in covered_roles)
            grouping = "E" + (
                "F" if has_spouse and has_other
                else "S" if has_spouse
                else "C" if has_other
                else "O"
            )
            plan = block.plans.get(mp.plan_code or "")
            ov = overrides.get((emp.id, block.product.id))
            marker = _OPTION_MARKER.search(f"{mp.category_display or ''}")
            per_product[block.product.id] = EmployeeCoverage(
                plan_code=mp.plan_code,
                plan_label=(
                    (plan.report_label or plan.display_name) if plan else None
                ),
                basis_display=(
                    _money(eligible) if eligible is not None
                    else (str(basis_raw) if basis_raw else None)
                ),
                eligible=eligible,
                covered_dependant_ids=covered,
                grouping=grouping,
                option_marker=marker.group(1) if marker else None,
                dependant_option_ids=(ov.dependant_option_ids if ov else None),
            )
        coverage[emp.id] = per_product
    return coverage, deps_by_emp


def _dependant_amount(
    block: ProductBlock, cov: EmployeeCoverage, role: str | None
) -> float | None:
    """Resolve the option level covering a dependant of ``role``: the marker
    linked to the employee's own option, else the sole level, else the level
    elected via ``dependant_option_ids``. None = unresolvable (blank cell)."""
    if role is None:
        return None
    opts = block.role_options.get(role) or []
    if not opts:
        return None
    if cov.option_marker:
        for o in opts:
            if o.marker == cov.option_marker:
                return o.basis
    if len(opts) == 1:
        return opts[0].basis
    chosen = (cov.dependant_option_ids or {}).get(role)
    for o in opts:
        if o.category_id == chosen:
            return o.basis
    return None


def _policy_period(py: PolicyYear) -> str:
    def fmt(d: date | None) -> str:
        return f"{d.day} {d:%b %Y}" if d else ""

    # Join the present bounds with " to "; never `.strip(" to")`, which strips
    # the character SET {space, t, o} and could truncate a formatted date.
    return " to ".join(p for p in (fmt(py.start_date), fmt(py.end_date)) if p)


def _flag(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _prior_year_people(db: Session, py: PolicyYear) -> set[str] | None:
    """staff_ids + normalized NRICs on the client's previous policy year, or
    None when no previous year exists (flag column stays blank)."""
    prev = db.execute(
        select(PolicyYear)
        .where(PolicyYear.client_id == py.client_id, PolicyYear.year < py.year)
        .order_by(PolicyYear.year.desc())
    ).scalars().first()
    if prev is None:
        return None
    people: set[str] = set()
    for emp in db.execute(
        select(Employee).where(Employee.policy_year_id == prev.id)
    ).scalars().all():
        people.add(emp.staff_id)
        nric = emp.national_id_normalized or nric_from_attrs(emp.attribute_values)
        if nric:
            people.add(nric)
    return people


def _prior_cover_flag(
    emp: Employee, prior_people: set[str] | None
) -> str:
    explicit = (emp.attribute_values or {}).get("prior_year_cover")
    # Only a real boolean (yes/no normalized on ingest) is an authoritative
    # answer. Unrecognized free-text ("Unknown"/"TBD" kept raw by the parser)
    # must NOT short-circuit the prior-year computation — fall through to it.
    if isinstance(explicit, bool):
        return _flag(explicit)
    if prior_people is None:
        return ""
    nric = emp.national_id_normalized or nric_from_attrs(emp.attribute_values)
    return _flag(emp.staff_id in prior_people or bool(nric and nric in prior_people))


def _ident(attrs: dict, keys: tuple[str, ...], masked: bool) -> str:
    raw = first_value(attrs, keys)
    return mask_nric(raw) if masked else (raw or "")


def member_id_for_insurer(attrs: dict | None, insurer: str | None) -> str:
    """The member's ID with ``insurer`` from a roster ``insurer_member_ids`` map,
    tolerating casing drift between the roster column header and
    ``Product.insurer``. Returns "" when unknown."""
    if not insurer:
        return ""
    ids = (attrs or {}).get(INSURER_MEMBER_ID_KEY) or {}
    if insurer in ids:
        return str(ids[insurer])
    for name, value in ids.items():
        if str(name).strip().lower() == insurer.strip().lower():
            return str(value)
    return ""


def _member_id(attrs: dict, insurer: str) -> str:
    return member_id_for_insurer(attrs, insurer)


def insurer_product_blocks(
    db: Session, py: PolicyYear, insurer: str
) -> list[ProductBlock]:
    wanted = insurer.strip().lower()
    return [
        b for b in product_blocks(db, py)
        if (b.product.insurer or "").strip().lower() == wanted
    ]


def configured_insurers_for_year(db: Session, py: PolicyYear) -> list[str]:
    rows = db.execute(
        select(Product.insurer)
        .where(
            tenant_or_global(Product.client_id, py.client_id),
            Product.insurer.isnot(None),
        )
        .distinct()
    ).scalars().all()
    return sorted({str(v).strip() for v in rows if v and str(v).strip()})


def build_employee_listing(
    db: Session, py: PolicyYear, insurer: str, masked: bool = True
) -> Workbook:
    blocks = insurer_product_blocks(db, py, insurer)
    employees = report_employees(db, py)
    coverage, _ = _employee_coverage(db, py, employees, blocks)
    cases = load_cases(db, py.id)
    fcl_by_product = free_cover_limits(db, py.id)
    prior_people = _prior_year_people(db, py)
    period = _policy_period(py)

    header = [
        "Entity", "Staff ID", "Employee Name", "Identification No.",
        "Date of Birth", "Gender", "Marital Status", "Employment Status",
        "Designation", "Country of Work", "Foreigner Employment Pass",
        "Nationality", "Date of Hire", "Confirmation Date", "Effective Date",
        "Last Day of Service", "Category", "Job Grade", "Division",
        "Department", "Cost Centre", "Email Address", "Mobile Phone",
        "Bank Code", "Branch Code", "Bank Account No.",
        "Has Insurance Cover Last Year", "Eligible to Sell Leave",
        f"{insurer} Member ID", "Currency", "Monthly Salary", "Remarks",
        "Policy Period",
    ]
    for b in blocks:
        if b.lump_sum:
            header += [
                f"{b.report_code} EE Basis of Cover",
                f"{b.report_code} EE Eligible Sum Insured",
                f"{b.report_code} EE Sum Insured Pending U/W",
                f"{b.report_code} EE Last Accepted Sum Insured",
            ]
        else:
            header += [
                f"{b.report_code} Plan/Basis of Cover",
                f"{b.report_code} Family Grouping",
            ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    append_safe(ws, header)
    _bold_header(ws)

    for emp in employees:
        attrs = emp.attribute_values or {}
        row: list[object] = [
            first_value(attrs, ("entity", "company", "subsidiary")) or "",
            emp.staff_id,
            emp.employee_name or "",
            _ident(attrs, EMPLOYEE_ID_KEYS, masked),
            _as_date(first_value(attrs, ("date_of_birth", "dob"))),
            first_value(attrs, ("gender", "sex")) or "",
            first_value(attrs, ("marital_status",)) or "",
            first_value(attrs, ("employment_status",)) or "",
            first_value(attrs, ("designation",)) or "",
            first_value(attrs, ("country_of_work",)) or "",
            first_value(attrs, ("pass",)) or "",
            first_value(attrs, ("nationality",)) or "",
            _as_date(first_value(attrs, ("date_of_hire", "hire_date"))),
            _as_date(first_value(attrs, ("confirmation_date",))),
            _as_date(first_value(attrs, ("effective_date",))),
            _last_day_of_service(emp),
            first_value(attrs, ("category",)) or "",
            first_value(attrs, ("job_grade", "grade")) or "",
            first_value(attrs, ("division",)) or "",
            first_value(attrs, ("department",)) or "",
            first_value(attrs, ("cost_centre",)) or "",
            first_value(attrs, ("email", "email_address")) or "",
            first_value(attrs, ("mobile", "mobile_phone")) or "",
            first_value(attrs, ("bank_code",)) or "",
            first_value(attrs, ("branch_code",)) or "",
            first_value(attrs, ("bank_account_no",)) or "",
            _prior_cover_flag(emp, prior_people),
            _flag(leave_sell_eligible(emp)),
            _member_id(attrs, insurer),
            first_value(attrs, ("currency",)) or "",
            first_value(attrs, ("salary",)) or "",
            first_value(attrs, ("remarks",)) or "",
            period,
        ]
        per_product = coverage.get(emp.id, {})
        for b in blocks:
            cov = per_product.get(b.product.id)
            if b.lump_sum:
                if cov is None or cov.eligible is None:
                    row += [
                        cov.basis_display if cov else "",
                        "", "", "",
                    ]
                else:
                    pending, accepted = report_uw_amounts(
                        cov.eligible,
                        fcl_by_product.get(b.product.id),
                        cases.get((emp.id, b.product.id)),
                    )
                    row += [cov.basis_display, cov.eligible, pending, accepted]
            else:
                if cov is None:
                    row += ["No Coverage", "EO"]
                else:
                    row += [cov.plan_label or "", cov.grouping]
        append_safe(ws, row)

    _autosize(ws)
    return wb


def build_dependant_listing(
    db: Session, py: PolicyYear, insurer: str, masked: bool = True
) -> Workbook:
    blocks = insurer_product_blocks(db, py, insurer)
    employees = report_employees(db, py)
    emp_by_id = {e.id: e for e in employees}
    coverage, deps_by_emp = _employee_coverage(db, py, employees, blocks)
    cases = load_cases(db, py.id)
    fcl_by_product = free_cover_limits(db, py.id)
    period = _policy_period(py)

    role_blocks: list[tuple[ProductBlock, str]] = [
        (b, role)
        for b in blocks
        if b.lump_sum
        for role in ("spouse", "child")
        if role in b.role_options
    ]
    schedule_blocks = [b for b in blocks if not b.lump_sum]

    header = [
        "Entity", "Staff ID", "Employee Name",
        "Employee's Identification No.", "Dependant Name",
        "Dependant's Identification No.", "Relationship", "Date of Marriage",
        "Gender", "Date of Birth", "Effective Date", "Termination Date",
        "Remarks", f"{insurer} Member ID", "Deletion Date", "Policy Period",
    ]
    for b, role in role_blocks:
        code = f"{b.report_code} {_ROLE_CODES[role]}"
        header += [
            f"{code} Basis of Cover",
            f"{code} Eligible Sum Insured",
            f"{code} Sum Insured Pending U/W",
            f"{code} Last Accepted Sum Insured",
        ]
    for b in schedule_blocks:
        header += [
            f"{b.report_code} Plan/Basis of Cover",
            f"{b.report_code} Family Grouping",
        ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    append_safe(ws, header)
    _bold_header(ws)

    for emp in employees:
        per_product = coverage.get(emp.id, {})
        for dep in deps_by_emp.get(emp.id, []):
            dattrs = dep.attribute_values or {}
            role = classify_relationship(first_value(dattrs, REL_KEYS))
            cells: list[object] = []
            covered_any = False
            for b, brole in role_blocks:
                cov = per_product.get(b.product.id)
                if role != brole or cov is None:
                    cells += ["", "", "", ""]
                    continue
                if dep.id not in cov.covered_dependant_ids:
                    cells += ["No Coverage", 0, 0, 0]
                    continue
                # Covered by this product → the dependant belongs on the listing
                # even when the specific option level can't be resolved without
                # a per-member election. Emit the row with blank sum-insured
                # cells (the broker/enrollment supplies the level) rather than
                # silently dropping a covered life the insurer must bill.
                covered_any = True
                amount = _dependant_amount(b, cov, role)
                if amount is None:
                    cells += ["", "", "", ""]
                else:
                    pending, accepted = report_uw_amounts(
                        amount,
                        fcl_by_product.get(b.product.id),
                        cases.get((dep.id, b.product.id)),
                    )
                    cells += [_money(amount), amount, pending, accepted]
            for b in schedule_blocks:
                cov = per_product.get(b.product.id)
                if cov is not None and dep.id in cov.covered_dependant_ids:
                    covered_any = True
                    cells += [cov.plan_label or "", cov.grouping]
                else:
                    cells += ["", ""]
            if not covered_any:
                continue
            append_safe(ws, [
                first_value(dattrs, ("entity",)) or "",
                emp.staff_id,
                emp.employee_name or "",
                _ident(
                    emp_by_id[emp.id].attribute_values or {},
                    EMPLOYEE_ID_KEYS, masked,
                ),
                first_value(dattrs, ("dependant_name", "name")) or "",
                _ident(dattrs, DEPENDANT_ID_KEYS, masked),
                first_value(dattrs, ("relationship", "relation")) or "",
                _as_date(first_value(dattrs, ("date_of_marriage",))),
                first_value(dattrs, ("gender", "sex")) or "",
                _as_date(first_value(dattrs, ("date_of_birth", "dob"))),
                _as_date(first_value(dattrs, ("effective_date",))),
                _as_date(first_value(dattrs, ("termination_date",))),
                first_value(dattrs, ("remarks",)) or "",
                _member_id(dattrs, insurer),
                dep.terminated_effective,
                period,
                *cells,
            ])

    _autosize(ws)
    return wb


def eligible_amounts(
    db: Session, py: PolicyYear
) -> dict[tuple[str, str, bool], float]:
    """Eligible SI per underwritten life on lump-sum products, for the
    underwriting sync: {(subject_id, product_id, is_employee): amount}."""
    blocks = [b for b in product_blocks(db, py) if b.lump_sum]
    if not blocks:
        return {}
    employees = report_employees(db, py)
    coverage, deps_by_emp = _employee_coverage(db, py, employees, blocks)
    dep_role: dict[str, str | None] = {}
    for deps in deps_by_emp.values():
        for d in deps:
            dep_role[d.id] = classify_relationship(
                first_value(d.attribute_values or {}, ("relationship", "relation"))
            )

    out: dict[tuple[str, str, bool], float] = {}
    for emp in employees:
        for b in blocks:
            cov = coverage.get(emp.id, {}).get(b.product.id)
            if cov is None:
                continue
            if cov.eligible is not None:
                out[(emp.id, b.product.id, True)] = cov.eligible
            for dep_id in cov.covered_dependant_ids:
                amount = _dependant_amount(b, cov, dep_role.get(dep_id))
                if amount is not None:
                    out[(dep_id, b.product.id, False)] = amount
    return out


def build_readiness(db: Session, py: PolicyYear) -> dict:
    """Config gaps the Reports page surfaces before insurer listings run."""
    blocks = product_blocks(db, py)
    insurers = configured_insurers_for_year(db, py)
    employees = report_employees(db, py)

    missing_labels = [
        {"product_code": b.report_code, "plan_code": code}
        for b in blocks
        if not b.lump_sum and b.product.insurer
        for code, plan in sorted(b.plans.items())
        if not plan.report_label
    ]
    missing_member_ids = {
        ins: sum(
            1 for e in employees
            if not _member_id(e.attribute_values or {}, ins)
        )
        for ins in insurers
    }
    return {
        "insurers": insurers,
        "products_without_insurer": sorted(
            b.report_code for b in blocks if not b.product.insurer
        ),
        "plans_missing_report_label": missing_labels,
        "employees_missing_nric": sum(
            1 for e in employees
            if not (
                e.national_id_normalized or nric_from_attrs(e.attribute_values)
            )
        ),
        "employees_missing_member_id": missing_member_ids,
        "employee_count": len(employees),
    }
