"""Deletion impact and launch readiness for one benefit year."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    BulkPlanUpdate,
    Category,
    Claim,
    Dependant,
    DualCoverageDecision,
    Employee,
    EmployeePlanOverride,
    Enrollment,
    EnrollmentWindow,
    FlexPricing,
    FlexScheme,
    LeaveElection,
    LeavePolicy,
    MemberEnquiry,
    PlacementSlipRow,
    Plan,
    PolicyYearCard,
    PolicyYearPanel,
    ProductSetup,
    ProductTerm,
    ReportVersion,
    UnderwritingCase,
    UnderwritingReview,
)
from app.models.category import CategoryStatus
from app.models.flex_scheme import FlexSchemeStatus
from app.models.product_setup import ProductSetupStatus


@dataclass(frozen=True)
class Dependency:
    key: str
    model: Any
    operational: bool


DEPENDENCIES = (
    Dependency("categories", Category, False),
    Dependency("plans", Plan, False),
    Dependency("product_terms", ProductTerm, False),
    Dependency("product_setups", ProductSetup, False),
    Dependency("flex_schemes", FlexScheme, False),
    Dependency("flex_pricing", FlexPricing, False),
    Dependency("leave_policies", LeavePolicy, False),
    Dependency("panel_selections", PolicyYearPanel, False),
    Dependency("panel_cards", PolicyYearCard, False),
    Dependency("placement_slips", PlacementSlipRow, True),
    Dependency("employees", Employee, True),
    Dependency("dependants", Dependant, True),
    Dependency("claims", Claim, True),
    Dependency("enrollment_windows", EnrollmentWindow, True),
    Dependency("enrollments", Enrollment, True),
    Dependency("leave_elections", LeaveElection, True),
    Dependency("plan_overrides", EmployeePlanOverride, True),
    Dependency("bulk_updates", BulkPlanUpdate, True),
    Dependency("underwriting_reviews", UnderwritingReview, True),
    Dependency("underwriting_cases", UnderwritingCase, True),
    Dependency("reports", ReportVersion, True),
    Dependency("member_enquiries", MemberEnquiry, True),
    Dependency("dual_coverage_decisions", DualCoverageDecision, True),
)


def _count(db: Session, model: Any, policy_year_id: str) -> int:
    value = db.scalar(
        select(func.count()).select_from(model).where(model.policy_year_id == policy_year_id)
    )
    return int(value or 0)


def deletion_counts(db: Session, policy_year_id: str) -> tuple[dict[str, int], int]:
    counts = {dep.key: _count(db, dep.model, policy_year_id) for dep in DEPENDENCIES}
    operational = sum(counts[dep.key] for dep in DEPENDENCIES if dep.operational)
    return counts, operational


def readiness(db: Session, policy_year_id: str) -> tuple[dict[str, int], list[str], list[str]]:
    plans = _count(db, Plan, policy_year_id)
    review_plans = int(
        db.scalar(
            select(func.count())
            .select_from(Plan)
            .where(
                Plan.policy_year_id == policy_year_id,
                Plan.status != "confirmed",
            )
        )
        or 0
    )
    categories = _count(db, Category, policy_year_id)
    setups = _count(db, ProductSetup, policy_year_id)
    confirmed_setups = int(
        db.scalar(
            select(func.count())
            .select_from(ProductSetup)
            .where(
                ProductSetup.policy_year_id == policy_year_id,
                ProductSetup.status == ProductSetupStatus.confirmed,
            )
        )
        or 0
    )
    review_categories = int(
        db.scalar(
            select(func.count())
            .select_from(Category)
            .where(
                Category.policy_year_id == policy_year_id,
                Category.status != CategoryStatus.confirmed.value,
            )
        )
        or 0
    )
    scheme = db.scalar(select(FlexScheme).where(FlexScheme.policy_year_id == policy_year_id))
    flex_confirmed = int(scheme is not None and scheme.status == FlexSchemeStatus.confirmed)
    employees = _count(db, Employee, policy_year_id)
    leave_policy = _count(db, LeavePolicy, policy_year_id)
    metrics = {
        "plans": plans,
        "plans_needing_review": review_plans,
        "categories": categories,
        "product_setups": setups,
        "confirmed_product_setups": confirmed_setups,
        "categories_needing_review": review_categories,
        "confirmed_flex_schemes": flex_confirmed,
        "leave_policies": leave_policy,
        "employees": employees,
    }
    blockers: list[str] = []
    warnings: list[str] = []
    if plans == 0 and flex_confirmed == 0:
        blockers.append("Configure and confirm at least one insured product or Flex scheme.")
    if setups > confirmed_setups:
        blockers.append("Confirm every product setup carried by this benefit year.")
    if review_plans:
        blockers.append("Review and confirm every benefit plan.")
    if review_categories:
        blockers.append("Review and confirm every employee category.")
    if employees == 0:
        warnings.append("No member roster has been loaded for this benefit year.")
    if scheme is not None and leave_policy == 0:
        warnings.append("Flex is configured without a buy/sell leave policy.")
    return metrics, blockers, warnings
