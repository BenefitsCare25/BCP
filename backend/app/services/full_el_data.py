"""Batch-load the company snapshot and collect actionable Full EL gaps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from functools import cached_property
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    Client,
    Dependant,
    Employee,
    EmployeePlanOverride,
    Plan,
    PolicyYear,
    Product,
    ProductSetup,
)
from app.models.placement_slip import PlacementSlipRow
from app.models.underwriting_case import UnderwritingReview
from app.services.built_in_listings import listing_employees
from app.services.coverage_resolver import load_overrides
from app.services.el_report_rules import ReportRules, mapping, positive_number
from app.services.enrollment_products import resolve_products_by_codes
from app.services.insurer_listings import EmployeeCoverage, ProductBlock, _employee_coverage
from app.services.insurer_listings import product_blocks as listing_blocks
from app.services.product_terms import ResolvedTerm, resolve_terms
from app.services.underwriting import CaseMap, load_cases


@dataclass
class Gap:
    product: str
    field: str
    issue: str
    action: str
    source: str = ""
    count: int = 0
    staff_ids: list[str] = dataclass_field(default_factory=list)


@dataclass
class ProductProfile:
    block: ProductBlock
    setup: ProductSetup | None
    categories: list[Category]
    rules: ReportRules
    source_label: str = ""

    @property
    def term(self) -> ResolvedTerm:
        if self.block.term is None:
            raise ValueError("Full EL product profile requires resolved policy terms")
        return self.block.term

    @cached_property
    def source_text(self) -> str:
        return json.dumps({k: self.answers.get(k) for k in ("terms", "sections")}).lower()

    def rate_period_error(self, year: PolicyYear) -> str:
        schedules = self.answers.get("source_rate_schedules")
        if not isinstance(schedules, list) or not schedules:
            return ""
        selected = [s for s in schedules if isinstance(s, dict) and s.get("selected") is True]
        if len(selected) != 1:
            return "Source rate period selection requires review"
        from datetime import date

        schedule = selected[0]
        if schedule.get("start_date") and schedule.get("end_date"):
            try:
                start = date.fromisoformat(schedule["start_date"])
                end = date.fromisoformat(schedule["end_date"])
            except (ValueError, TypeError):
                return "Source rate period dates are invalid"
            reference = max(year.start_date, self.term.coverage_start)
            if not start <= reference <= end:
                return "Selected rates do not cover this benefit year's product start"
        return ""

    @property
    def answers(self) -> dict[str, Any]:
        return mapping(self.setup.answers) if self.setup else {}

    @property
    def reviewed(self) -> bool:
        return bool(self.setup and self.setup.status == "confirmed")

    @property
    def source(self) -> str:
        if self.source_label:
            return self.source_label
        return (
            f"Placement slip {self.setup.origin_ref}"
            if self.setup and self.setup.origin_ref
            else ""
        )


@dataclass
class FullELData:
    year: PolicyYear
    company: str
    employees: list[Employee]
    dependants: list[Dependant]
    profiles: list[ProductProfile]
    coverage: dict[str, dict[str, EmployeeCoverage]]
    cases: CaseMap
    reviews: dict[str, UnderwritingReview]
    categories: dict[str, Category]
    overrides: dict[tuple[str, str], EmployeePlanOverride]
    dependants_by_id: dict[str, Dependant] = dataclass_field(default_factory=dict)
    gaps: dict[tuple[str, str, str], Gap] = dataclass_field(default_factory=dict)

    def gap(
        self,
        product: str,
        field_name: str,
        issue: str,
        action: str,
        source: str = "",
        staff_id: str | None = None,
    ) -> None:
        key = (product, field_name, issue)
        gap = self.gaps.setdefault(key, Gap(product, field_name, issue, action, source))
        gap.count += 1
        if staff_id and staff_id not in gap.staff_ids and len(gap.staff_ids) < 10:
            gap.staff_ids.append(staff_id)


def _profiles(db: Session, py: PolicyYear, categories: list[Category]) -> list[ProductProfile]:
    setups = list(db.scalars(select(ProductSetup).where(ProductSetup.policy_year_id == py.id)))
    by_code = {s.product_code.upper(): s for s in setups}
    blocks = {b.product.code.upper(): b for b in listing_blocks(db, py)}
    plans = list(db.scalars(select(Plan).where(Plan.policy_year_id == py.id)))
    terms = {t.product_id: t for t in resolve_terms(db, py)}
    products = resolve_products_by_codes(db, py, set(by_code) | {t.code for t in terms.values()})
    for code in sorted(set(by_code) | {t.code.upper() for t in terms.values()}):
        if code in blocks:
            continue
        setup = by_code.get(code)
        product = products.get(code) or Product(
            id=f"setup:{setup.id}" if setup else f"unresolved:{code}",
            code=code,
            display_name=code,
            client_id=py.client_id,
            has_dependants=False,
        )
        header = mapping(mapping(setup.answers).get("header")) if setup else {}
        blocks[code] = ProductBlock(
            product=product,
            report_code=code,
            lump_sum=product.line == "life",
            insurer=str(header.get("insurer") or ""),
            term=terms.get(product.id),
            plans={p.code: p for p in plans if p.product_id == product.id},
        )
    profiles = []
    for code, block in sorted(blocks.items()):
        if block.term is None:
            block.term = ResolvedTerm(
                block.product.id,
                code,
                block.product.display_name,
                py.start_date,
                py.end_date,
                True,
            )
        setup = by_code.get(code)
        profiles.append(
            ProductProfile(
                block,
                setup,
                [c for c in categories if c.product_id == block.product.id],
                ReportRules.from_answers(mapping(setup.answers) if setup else {}),
            )
        )
    return profiles


def load_full_el(
    db: Session,
    py: PolicyYear,
    employee_status: str,
) -> FullELData:
    employees = listing_employees(db, py, employee_status)
    categories = list(db.scalars(select(Category).where(Category.policy_year_id == py.id)))
    profiles = _profiles(db, py, categories)
    coverage, _ = _employee_coverage(db, py, employees, [p.block for p in profiles])
    ids = {e.id for e in employees}
    dependants = [
        d
        for d in db.scalars(
            select(Dependant).where(Dependant.policy_year_id == py.id).order_by(Dependant.id)
        )
        if d.employee_id in ids or (not d.employee_id and employee_status == "all")
    ]
    client = db.get(Client, py.client_id)
    data = FullELData(
        py,
        (client.legal_name or client.name) if client else "",
        employees,
        dependants,
        profiles,
        coverage,
        load_cases(db, py.id),
        {
            r.id: r
            for r in db.scalars(
                select(UnderwritingReview).where(UnderwritingReview.policy_year_id == py.id)
            )
        },
        {c.id: c for c in categories},
        load_overrides(db, py.id, list(ids)),
    )
    for profile in profiles:
        _setup_gaps(data, profile)
    data.dependants_by_id = {d.id: d for d in dependants}
    slips = {
        s.id: s
        for s in db.scalars(
            select(PlacementSlipRow).where(PlacementSlipRow.policy_year_id == py.id)
        )
    }
    for profile in profiles:
        source = slips.get(profile.setup.origin_ref or "") if profile.setup else None
        if source:
            profile.source_label = f"{source.filename} ({source.id})"
            if not source.blob_url:
                data.gap(
                    profile.block.product.code,
                    "Source file",
                    "Original upload is not retained; report uses the saved extraction",
                    "Retain the original placement slip for source verification.",
                    profile.source,
                )
    if not profiles:
        data.gap("", "Products", "No products configured", "Upload and review the placement slip.")
    return data


def _setup_gaps(data: FullELData, p: ProductProfile) -> None:
    code, answers = p.block.product.code, p.answers

    def gap(field_name: str, issue: str, action: str) -> None:
        data.gap(code, field_name, issue, action, p.source)

    if not p.reviewed:
        gap(
            "Setup",
            "Setup is draft or absent; member premiums are withheld",
            "Review and confirm this company's product setup.",
        )
    if not p.categories:
        gap(
            "Categories",
            "No coverage categories materialized",
            "Confirm setup and map eligibility.",
        )
    if not p.block.insurer:
        gap("Insurer", "Insurer is missing", "Complete Header & Policy in Product Setup.")
    for name, value in (
        ("Administration", p.rules.admin),
        ("Age convention", p.rules.age_basis),
        ("Age reference", p.rules.age_reference),
        ("Currency", p.rules.currency),
    ):
        if not value:
            gap(name, f"{name} is not established", "Review the Full EL fields in Header & Policy.")
    if p.block.lump_sum and not p.rules.premium_si_basis:
        gap(
            "Premium SI basis",
            "Eligible versus accepted SI billing basis is not established",
            "Record the insurer's premium basis; benefit payout wording is insufficient.",
        )
    source_text = p.source_text
    if "headcount" in p.rules.admin.lower() and "name basis" in source_text:
        if not p.rules.admin_resolution:
            gap(
                "Administration",
                "Headcount header includes a name-basis movement clause",
                "Record the reviewed administration exceptions in Header & Policy.",
            )
    for issue in (answers.get("source_issues") or []) if not answers.get("source_reviewed") else []:
        gap(
            "Source review", str(issue), "Resolve the placement-slip source issue in Product Setup."
        )
    _category_gaps(data, p)
    term = p.block.term
    if term and term.gst_included is None:
        gap(
            "GST",
            "GST treatment is not explicitly configured",
            "Set taxable or exempt in Policy Terms.",
        )
    if term and term.gst_included and term.gst_rate is None:
        gap(
            "GST",
            "GST rate is not explicitly configured",
            "Record the rate from the reviewed setup.",
        )
    if term and "non" in source_text and "evidence" in source_text:
        if term.free_cover_limit is None or term.nel_age_limit is None:
            gap(
                "Underwriting limits",
                "Source NEL wording has not been fully applied to Policy Terms",
                "Review the amount and ANB age threshold in Policy Terms "
                "before acceptance or billing.",
            )
    for plan in p.block.plans.values():
        if not plan.report_label:
            gap(
                "Plan label",
                f"Plan {plan.code} has no report label",
                "Review its insurer-facing plan label; the stored plan name is shown meanwhile.",
            )
    period_error = p.rate_period_error(data.year)
    if period_error:
        gap("Rate period", period_error, "Review the selected renewal rate schedule.")


def _category_gaps(data: FullELData, p: ProductProfile) -> None:
    for cat in p.categories:
        pa = mapping(cat.plan_assignments)
        tiers = mapping(pa.get("rate_tiers"))
        bands = pa.get("voluntary_rates") or []
        has_rate = (
            positive_number(pa.get("premium_rate")) is not None
            or any(positive_number(mapping(t).get("rate")) is not None for t in tiers.values())
            or any(positive_number(mapping(b).get("rate")) is not None for b in bands)
            or (
                pa.get("rate_basis") == "annual_flat"
                and positive_number(pa.get("annual_premium")) is not None
            )
        )
        if not has_rate:
            data.gap(
                p.block.product.code,
                "Category rate",
                "Category has no usable annual rate",
                "Review the category rate against the selected placement-slip schedule.",
                cat.source_ref or "",
            )
        if cat.status != "confirmed" or cat.rule_status not in (None, "validated"):
            data.gap(
                p.block.product.code,
                "Category mapping",
                "Coverage mapping is not confirmed",
                "Review the category and validate its company eligibility rule.",
                cat.source_ref or "",
            )


def matched_category(data: FullELData, emp: Employee, p: ProductProfile) -> Category | None:
    pid = p.block.product.id
    override = data.overrides.get((emp.id, pid))
    if override and override.tier_category_id:
        cat = data.categories.get(override.tier_category_id)
        return cat if cat and cat.product_id == pid else None
    for match in emp.matched_categories or []:
        cat = data.categories.get(match.get("category_id") or "")
        if cat and cat.product_id == pid:
            return cat
    return None
