"""Everything the Full Employee Listing reads, loaded once per export."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import tenant_or_global
from app.models import (
    Category,
    Client,
    Dependant,
    Employee,
    PolicyYear,
    Product,
    ProductSetup,
    ProductTerm,
)
from app.models.employee import EMPLOYEE_STATUS_ACTIVE
from app.models.employee_listing import ElLayoutProfile, ListingAssignment
from app.models.product_setup import ProductSetupStatus
from app.models.product_term import DEFAULT_GST_RATE
from app.services.el_import.mapping import normalize_label
from app.services.el_report_rules import (
    ReportRules,
    positive_number,
    reviewed_si_caps,
    source_maximum,
)
from app.services.el_workbook import ElLayout
from app.services.eligibility_mapping import category_signature
from app.services.full_el.layout import default_layout, layout_from_json
from app.services.underwriting import CaseMap, free_cover_limits, load_cases

_MULTIPLE = re.compile(r"(\d+(?:\.\d+)?)\s*x\b", re.IGNORECASE)


@dataclass
class CategoryInfo:
    category: Category
    product: Product
    label: str  # the listing's own wording, else numbered slip wording
    plan_code: str | None
    rate_basis: str | None
    rate: float | None
    tiers: dict[str, float]
    salary_multiple: float | None
    flat_si: float | None
    covers_dependants: bool


@dataclass
class ProductInfo:
    product: Product
    insurer: str
    policy_no: str
    admin_basis: str
    eligibility: dict[str, Any]
    period: tuple[date | None, date | None]
    gst_factor: float | None  # None = unknown
    max_si: float | None  # the reviewed cap shared with listings, plans and flex
    rules: ReportRules  # reviewed Full EL rules; slip wording only where unset
    nel_amount: float | None
    nel_age: int | None
    confirmed: bool


@dataclass
class ElContext:
    year: PolicyYear
    client: Client
    layout: ElLayout
    block_products: dict[int, list[str]]
    products: dict[str, ProductInfo]
    categories: dict[str, CategoryInfo]
    employees: list[Employee]
    dependants: dict[str, list[Dependant]]
    listed: dict[tuple[str, str], ListingAssignment]  # (member_key, product_id)
    age_basis: str
    reference_date: date
    listing_source: str | None
    gaps: list[tuple[str, str, str]] = field(default_factory=list)
    # Underwriting state, loaded only when a product bills on accepted SI.
    uw_cases: CaseMap = field(default_factory=dict)  # (subject_id, product_id)
    uw_fcls: dict[str, float] = field(default_factory=dict)  # product_id -> FCL


def _gst_factor(term: ProductTerm | None, answers: dict[str, Any]) -> float | None:
    if term is not None and term.gst_included is not None:
        rate = term.gst_rate if term.gst_rate is not None else DEFAULT_GST_RATE
        return 1.0 + float(rate) / 100.0 if term.gst_included else 1.0
    selected = [s for s in answers.get("source_rate_schedules") or []
                if isinstance(s, dict) and s.get("selected")]
    heading = " ".join(str(c) for s in selected for r in (s.get("source_rows") or [])[:3]
                       for c in (r or []) if c)
    if re.search(r"gst\s+exempt", heading, re.I):
        return 1.0
    if re.search(r"subj(?:ect)?\.?\s*to\s+gst", heading, re.I):
        return 1.0 + DEFAULT_GST_RATE / 100.0
    return None


def _age(value: Any) -> int | None:
    number = positive_number(value)
    return int(number) if number else None


def load_context(
    db: Session, year: PolicyYear, *, employee_status: str = "all"
) -> ElContext:
    client = db.get(Client, year.client_id)
    if client is None:
        raise LookupError(f"Policy year {year.id} has no client record.")
    categories = list(db.execute(
        select(Category).where(Category.policy_year_id == year.id).order_by(Category.priority)
    ).scalars())
    product_ids = {c.product_id for c in categories if c.product_id}
    products = {
        p.code: p for p in db.execute(
            select(Product).where(Product.id.in_(product_ids),
                                  tenant_or_global(Product.client_id, year.client_id))
        ).scalars()
    }
    by_id = {p.id: p for p in products.values()}
    setups = {s.product_code.upper(): s for s in db.execute(
        select(ProductSetup).where(ProductSetup.policy_year_id == year.id)
    ).scalars()}
    terms = {t.product_id: t for t in db.execute(
        select(ProductTerm).where(ProductTerm.policy_year_id == year.id)
    ).scalars()}
    rate_bases: dict[str, set[str]] = {}
    for c in categories:
        if c.product_id in by_id:
            basis = (c.plan_assignments or {}).get("rate_basis")
            rate_bases.setdefault(by_id[c.product_id].code, set()).add(str(basis or ""))

    profile = db.execute(
        select(ElLayoutProfile).where(ElLayoutProfile.client_id == year.client_id)
    ).scalar_one_or_none()
    if profile is not None:
        layout = layout_from_json(profile.layout)
        block_products = {
            int(k): [c for c in v if c in products]
            for k, v in (profile.block_products or {}).items()
        }
        label_map = profile.label_map or {}
    else:
        layout, block_products = default_layout(list(products.values()), rate_bases)
        label_map = {}

    infos: dict[str, ProductInfo] = {}
    caps = reviewed_si_caps(db, year.id)
    gaps: list[tuple[str, str, str]] = []
    for code, product in products.items():
        setup = setups.get(code.upper())
        answers = setup.answers if setup and isinstance(setup.answers, dict) else {}
        header = answers.get("header") or {}
        eligibility = answers.get("eligibility") or {}
        term = terms.get(product.id)
        rules = ReportRules.from_answers(answers)
        infos[code] = ProductInfo(
            product=product,
            insurer=str(header.get("insurer") or product.insurer or ""),
            policy_no=re.sub(r"\.0+$", "", str(header.get("policy_no") or
                                                (term.policy_number if term else "") or "")),
            admin_basis=str(header.get("admin_basis") or ""),
            eligibility=eligibility,
            period=(term.coverage_start if term else None, term.coverage_end if term else None),
            gst_factor=_gst_factor(term, answers),
            max_si=caps.get(code.upper()),
            rules=rules,
            nel_amount=(term.free_cover_limit if term and term.free_cover_limit else
                        _nel_amount(answers)),
            nel_age=(term.nel_age_limit if term and term.nel_age_limit else
                     _age(eligibility.get("age_limit_no_underwriting"))),
            confirmed=bool(setup and setup.status == ProductSetupStatus.confirmed),
        )
        gaps.extend(_rule_gaps(infos[code], source_maximum(answers)))

    cat_infos = _category_infos(categories, by_id, block_products, label_map)
    employees = list(db.execute(
        select(Employee).where(
            Employee.policy_year_id == year.id,
            *( [Employee.status == EMPLOYEE_STATUS_ACTIVE] if employee_status == "active" else []),
        )
    ).scalars())
    dependants: dict[str, list[Dependant]] = {}
    for dep in db.execute(select(Dependant).where(Dependant.policy_year_id == year.id)).scalars():
        if dep.employee_id and (employee_status != "active" or dep.status == "active"):
            dependants.setdefault(dep.employee_id, []).append(dep)
    listed = {
        (a.member_key, a.product_id): a
        for a in db.execute(
            select(ListingAssignment).where(ListingAssignment.policy_year_id == year.id)
        ).scalars()
    }
    age_bases = {i.rules.age_basis for i in infos.values() if i.rules.age_basis}
    accepted_basis = any(i.rules.premium_si_basis == "Accepted SI" for i in infos.values())
    return ElContext(
        year=year, client=client, layout=layout, block_products=block_products,
        products=infos, categories=cat_infos, employees=employees, dependants=dependants,
        listed=listed,
        age_basis="ALB" if age_bases == {"ALB"} else "ANB",
        reference_date=year.start_date,
        listing_source=profile.source_filename if profile else None,
        gaps=gaps,
        uw_cases=load_cases(db, year.id) if accepted_basis else {},
        uw_fcls=free_cover_limits(db, year.id) if accepted_basis else {},
    )


def _rule_gaps(info: ProductInfo, slip_cap: float | None) -> list[tuple[str, str, str]]:
    """Reviewed rules the listing cannot apply as written, and unreviewed caps."""
    code, rules = info.product.code, info.rules
    gaps = []
    if info.max_si is None and slip_cap is not None:
        gaps.append((code, f"Slip maximum SI {slip_cap:,.0f} not reviewed",
                     "Sums insured are not capped; confirm the maximum in the product setup."))
    if rules.age_reference == "Product cover start" and info.period[0] is None:
        gaps.append((code, "Product cover start unknown",
                     "Ages use the benefit-year start; set the cover start on the product terms."))
    return gaps


def _nel_amount(answers: dict[str, Any]) -> float | None:
    from app.services.el_report_rules import source_policy_terms

    return source_policy_terms(answers).get("free_cover_limit")


def _category_infos(
    categories: list[Category],
    products: dict[str, Product],
    block_products: dict[int, list[str]],
    label_map: dict[str, Any],
) -> dict[str, CategoryInfo]:
    """Per category: pricing, sum-insured basis and the label the listing uses."""
    labels: dict[tuple[str, str, str], str] = {}
    for block in label_map.values():
        for entry in (block or {}).values():
            if isinstance(entry, dict) and entry.get("product_code"):
                labels[(entry["product_code"], entry.get("category_signature") or "",
                        str(entry.get("plan_code") or ""))] = str(entry.get("label") or "")
    block_of = {code: i for i, codes in block_products.items() for code in codes}
    numbering: dict[int, int] = {}
    out: dict[str, CategoryInfo] = {}
    for c in categories:
        product = products.get(c.product_id or "")
        if product is None:
            continue
        pa = c.plan_assignments or {}
        plan = str(pa.get("plan_code") or "") or None
        signature = category_signature(c.raw_description or c.display_name)
        block = block_of.get(product.code, -1)
        numbering[block] = numbering.get(block, 0) + 1
        listed = labels.get((product.code, signature, plan or ""))
        basis = str(pa.get("basis") or "")
        multiple = _MULTIPLE.search(basis)
        flat = None if multiple else positive_number(basis.replace(",", "").replace("$", ""))
        tiers = {
            k: float(v.get("rate") or 0) for k, v in (pa.get("rate_tiers") or {}).items()
            if isinstance(v, dict) and v.get("rate")
        }
        text = f"{c.display_name} {c.raw_description or ''}"
        out[c.id] = CategoryInfo(
            category=c,
            product=product,
            label=listed or f"{numbering[block]}) {c.display_name}",
            plan_code=plan,
            rate_basis=pa.get("rate_basis"),
            rate=positive_number(pa.get("premium_rate")),
            tiers=tiers,
            salary_multiple=float(multiple.group(1)) if multiple else None,
            flat_si=flat,
            covers_dependants=bool(
                set(tiers) & {"ES", "EC", "EF"}
                or re.search(r"depend[ae]n[td]|spouse|child", text, re.I)
            ),
        )
    return out


def label_key(label: str) -> str:
    return normalize_label(label)
