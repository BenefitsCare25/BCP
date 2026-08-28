"""EnrollmentWindow — a configured enrollment period within a policy year.

A window is the time-box during which members (today: brokers acting on their
behalf) may change their benefit elections. Multiple windows can exist per policy
year (an annual ``open`` enrollment, a rolling ``new_hire`` window, ad-hoc
``life_event`` windows).

Reverse / passive enrollment lives here: when a window is opened, every eligible
employee gets an ``Enrollment`` pre-populated with their current (baseline)
elections. ``default_behavior`` decides what happens to members who never act by
the time the window closes — keep their current plan, or be deemed to decline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class WindowType:
    open = "open"  # annual open-enrollment for the whole roster
    new_hire = "new_hire"  # rolling window for newly-added staff
    life_event = "life_event"  # ad-hoc (marriage, birth, …)


class WindowStatus:
    draft = "draft"  # being configured, not yet open
    open = "open"  # accepting elections
    closed = "closed"  # finalized; elections projected to overrides


class DefaultBehavior:
    """What happens to an untouched enrollment when the window closes."""

    keep_current = "deemed_keep_current"  # baseline elections stand
    decline = "deemed_decline"  # member is deemed to decline coverage


class FlexPriceSource:
    """Where a product's flex "price tag" comes from (set per product at window
    creation). Both produce a price tag deducted from the member's flex wallet —
    they differ only in the source figure."""

    slip = "slip"  # the placement slip's premium for the elected tier
    manual = "manual"  # the portal-configured FlexPricing matrix amount


class FlexDrawdownRule:
    """When/how much flex is drawn down for coverage (company-wide per window)."""

    full = "full"  # deduct the member's whole plan price tag
    on_change = "on_change"  # deduct only the upgrade/downgrade difference vs default


class EnrollmentWindow(Base, TimestampMixin):
    __tablename__ = "enrollment_windows"
    __table_args__ = (
        Index("ix_enrollment_windows_year_status", "policy_year_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WindowType.open, server_default=WindowType.open
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WindowStatus.draft,
        server_default=WindowStatus.draft, index=True,
    )
    default_behavior: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DefaultBehavior.keep_current,
        server_default=DefaultBehavior.keep_current,
    )
    # Feature toggles for what this window permits members to change.
    allow_plan_change: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    allow_leave: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    allow_dependant_changes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Whether MEMBERS may see and use this enrolment period in the portal. Off,
    # the period runs broker-managed: the window is still open (brokers elect on
    # members' behalf, confirm, and close it as normal) but the portal's
    # enrolment surface stays dark — no "enrolment open" marker, no
    # /portal/enrollment payload, and every member write is refused. Resolved
    # through ``enrollment_elections.member_window_for``, which is what every
    # member-facing and preview call site must use; ``open_window_for`` answers
    # the different question "is a window open at all" and does NOT honour this.
    member_self_service: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # Which products are in scope. NULL = all confirmed products for the year.
    product_scope: Mapped[list[Any] | None] = mapped_column(JSON(), nullable=True)
    # Legacy per-product source retained for historical windows and older clients.
    # New unified price books leave this NULL: recommendations come from the slip
    # and any saved matrix field is an explicit override.
    flex_price_source: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    # Company-wide flex drawdown rule for this window. See ``FlexDrawdownRule``.
    flex_drawdown_rule: Mapped[str] = mapped_column(
        String(16), nullable=False,
        default=FlexDrawdownRule.full, server_default=FlexDrawdownRule.full,
    )
    # Whether elections may draw more flex than the member's wallet holds. Off
    # (the default), submit/confirm reject an overdrawn enrollment; on, the
    # negative balance is allowed (e.g. shortfall recovered via payroll).
    allow_overdraft: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
