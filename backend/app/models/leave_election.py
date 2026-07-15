"""LeaveElection — a member's buy/sell-leave choice within an enrollment.

One row per enrollment. Tracks the action (buy / sell / none) and the number of
days, validated against the policy-year ``LeavePolicy`` bounds + increment. No
monetary value is recorded — funding/pricing is a future extension.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class LeaveAction:
    none = "none"  # no leave trading
    buy = "buy"  # purchase extra leave days
    sell = "sell"  # sell back leave days


class LeaveElectionStatus:
    draft = "draft"
    confirmed = "confirmed"


class LeaveElection(Base, TimestampMixin):
    __tablename__ = "leave_elections"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_leave_election_enrollment"),
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
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LeaveAction.none, server_default=LeaveAction.none
    )
    days: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    # Signed flex-wallet impact snapshotted from the leave rate at election time:
    # buy = negative (spend), sell = positive (credit), None/0 = no priced leave.
    # Stable if the policy's leave_rates change later.
    flex_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=LeaveElectionStatus.draft,
        server_default=LeaveElectionStatus.draft,
    )
