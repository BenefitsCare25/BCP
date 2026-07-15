"""Employee (Layer 3)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

EMPLOYEE_STATUS_ACTIVE = "active"
# Soft-terminated via an ADC movement file. Kept for history (claims/enrollment
# stay intact) but excluded from coverage, matching, flex sizing, and reports.
EMPLOYEE_STATUS_TERMINATED = "terminated"


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    staff_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Portal login binding to public.member_accounts. Plain String, not an FK:
    # employees live in a firm schema and member_accounts in public — app-layer
    # integrity (portal_auth.resolve_member_employee) owns the link.
    member_account_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    employee_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    attribute_values: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    derived_attribute_values: Mapped[dict[str, Any]] = mapped_column(
        JSON(), nullable=False, default=dict
    )
    matched_category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_categories: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON(), nullable=True
    )
    # ── Flexible-Benefits assignment (persisted by app/services/flex_assignment) ──
    # Snapshot of the employee's resolved Flex wallet, written when a confirmed
    # Flex scheme is assigned. Mirrors the insured ``matched_*`` fields but for the
    # family-status-sized Flex wallet rather than a Category. NULL until assigned;
    # re-assignment refreshes them (and clears them for inactive/ineligible staff).
    flex_family_status: Mapped[str | None] = mapped_column(String(8), nullable=True)
    flex_tier_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    flex_wallet_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    flex_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # How family status was resolved: "dependants" | "roster" | "none".
    flex_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    flex_assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="csv_import")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # Canonicalized NRIC/FIN (roster_attributes.normalize_nric) — the person
    # identity key for dedup + ADC record resolution. Indexed, nullable (blank
    # for foreigners / IDs withheld); app-enforced-unique per policy year, never
    # DB-unique (must allow NULLs + the staff_id fallback).
    national_id_normalized: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Effective date of a soft-termination (ADC deletion). NULL while active.
    terminated_effective: Mapped[date | None] = mapped_column(Date, nullable=True)
