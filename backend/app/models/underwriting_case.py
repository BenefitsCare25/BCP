"""UnderwritingCase — per-member sum-insured acceptance above the free cover limit.

A case exists when a member's (or covered dependant's) eligible sum insured on
a product exceeds the product's free cover limit (``ProductTerm.free_cover_limit``)
— the amount above FCL needs the insurer's medical underwriting. The broker
records the outcome; insurer listings read it as:

    Last Accepted Sum Insured = accepted_si (pending → the auto-covered FCL)
    Sum Insured Pending U/W   = eligible_si - accepted_si while pending, else 0

One OPEN case per (subject, product); ``refresh_underwriting_cases`` keeps the
set in sync with resolved coverage. Decisions are broker-recorded (accepted /
declined) and audit-logged at the API layer.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class UnderwritingStatus:
    pending = "pending"  # awaiting insurer decision; accepted_si = FCL so far
    accepted = "accepted"  # insurer accepted accepted_si (≤ eligible)
    declined = "declined"  # excess declined; accepted_si stays at FCL


VALID_UW_STATUSES = frozenset(
    {UnderwritingStatus.pending, UnderwritingStatus.accepted, UnderwritingStatus.declined}
)


class UnderwritingCase(Base, TimestampMixin):
    __tablename__ = "underwriting_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Exactly one of employee_id / dependant_id is set (the underwritten life).
    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    dependant_id: Mapped[str | None] = mapped_column(
        ForeignKey("dependants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Snapshot of the eligible SI when the case was (re)synced.
    eligible_si: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Amount currently in force: FCL while pending, the insurer's figure once
    # decided. Never above eligible_si.
    accepted_si: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UnderwritingStatus.pending, index=True
    )
    decided_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
