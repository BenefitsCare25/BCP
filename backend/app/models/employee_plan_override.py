"""EmployeePlanOverride — sparse per-employee deviation from the cohort default.

By default an employee's plan per product is decided by their matched
``Category.plan_assignments``. An override row exists ONLY when a specific
employee's coverage differs from that cohort default — written by an enrollment
confirmation, a bulk plan update, or a manual admin edit.

This mirrors the ``ProductTerm`` sparse-storage pattern: members who never elect
keep the category default untouched, and the effective plan is resolved as
``override if present else category default`` (see services/coverage_resolver).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class OverrideSource:
    enrollment = "enrollment"  # projected from a confirmed/deemed enrollment
    bulk_update = "bulk_update"  # written by a bulk plan-update batch
    manual_admin = "manual_admin"  # one-off admin edit


class EmployeePlanOverride(Base, TimestampMixin):
    __tablename__ = "employee_plan_overrides"
    __table_args__ = (
        UniqueConstraint("employee_id", "product_id", name="uq_override_employee_product"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    # The elected plan tier. NULL when declined (declined=True).
    plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The elected cohort tier (Category), for tiers that share a plan_code
    # (e.g. GPA "Option N"). NULL = the matched cohort default tier. Recorded for
    # audit/identity; coverage still resolves from plan_code. Not an FK.
    tier_category_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    declined: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # Flex "price tag" deducted from the wallet for this coverage, snapshotted from
    # FlexPricing at projection time (tier x age band). NULL = no flex price.
    flex_price_tag: Mapped[float | None] = mapped_column(Float, nullable=True)
    covered_dependant_ids: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    # Elected freestanding dependant option LEVEL per role, ``{role: category_id}``
    # (see EnrollmentElection.dependant_option_ids). NULL = no level chosen.
    dependant_option_ids: Mapped[dict | None] = mapped_column(JSON(), nullable=True)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=OverrideSource.manual_admin,
        server_default=OverrideSource.manual_admin,
    )
    # Back-reference to the producing enrollment / bulk-update record.
    source_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
