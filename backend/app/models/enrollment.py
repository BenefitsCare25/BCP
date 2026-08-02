"""Enrollment + EnrollmentElection — a member's in-flight benefit choices.

``Enrollment`` is one member's session inside an ``EnrollmentWindow``; it holds a
``baseline_snapshot`` of their elections at window-open (the basis for reverse /
passive enrollment and for an audit diff) and a lifecycle status.

``EnrollmentElection`` is one row per product within an enrollment: the plan tier
the member elected (upgrade / downgrade / keep / enroll), or a decline, plus
which dependants they chose to cover for that product.

These are the *process* tables. On confirm (or deemed finalization at window
close) the elections are projected into ``EmployeePlanOverride`` — the *effective
state* read everywhere else. This mirrors the draft → confirm → materialize shape
used by ProductSetup and FlexScheme.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class EnrollmentStatus:
    not_started = "not_started"  # created at window-open, untouched
    in_progress = "in_progress"  # member has started editing
    submitted = "submitted"  # member submitted, awaiting confirm
    confirmed = "confirmed"  # projected to overrides (explicit action)
    deemed = "deemed"  # finalized by default_behavior at window close
    declined = "declined"  # member declined all coverage


class ElectionAction:
    keep = "keep"  # same plan as baseline
    upgrade = "upgrade"  # moved to a richer tier
    downgrade = "downgrade"  # moved to a leaner tier
    enroll = "enroll"  # took up coverage that had none
    decline = "decline"  # opted out of this product


class Enrollment(Base, TimestampMixin):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("window_id", "employee_id", name="uq_enrollment_window_employee"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    window_id: Mapped[str] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EnrollmentStatus.not_started,
        server_default=EnrollmentStatus.not_started, index=True,
    )
    # Effective elections captured when the window opened — the reverse-enrollment
    # default and the audit baseline. Shape: {"products": {code: {...}}, "leave": {...}}.
    baseline_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EnrollmentElection(Base, TimestampMixin):
    __tablename__ = "enrollment_elections"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "product_id", name="uq_election_enrollment_product"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("enrollments.id", ondelete="CASCADE"), nullable=False, index=True
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
    previous_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # NULL when declined.
    elected_plan_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The cohort tier (Category) the member elected. Distinguishes tiers that
    # share a plan_code (e.g. GPA "Option 1/2/3" all map to plan '1'); NULL keeps
    # the matched cohort default. Not an FK so a category re-parse can't cascade.
    tier_category_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ElectionAction.keep,
        server_default=ElectionAction.keep,
    )
    # Dependant ids the member chose to cover for this product.
    covered_dependant_ids: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    # Elected freestanding dependant option LEVEL per role, ``{role: category_id}``
    # (role ∈ spouse/child; the id is a dependant-scope Category). Only needed when
    # the slip lists multiple unlinked option levels (e.g. GTL Spouse S$20k/40k/60k)
    # — linked option rows (GPA markers, VDL composition) price without an election.
    # NULL = no level chosen. Not an FK so a category re-parse can't cascade.
    dependant_option_ids: Mapped[dict[str, str] | None] = mapped_column(JSON(), nullable=True)
    # Flex "price tag" deducted from the member's wallet for this election,
    # resolved from FlexPricing (tier x age band) and snapshotted at confirm so
    # reporting is stable if the matrix later changes. NULL = no flex price.
    flex_price_tag: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
