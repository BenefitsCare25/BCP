"""Underwriting — insurer-grouped reviews + per-product case lines.

A member (or covered dependant) whose eligible sum insured on a lump-sum
product exceeds the Non-Evidence Limit — the free cover limit
(``ProductTerm.free_cover_limit``) — or whose age is at/above the NEL age gate
(``ProductTerm.nel_age_limit``) needs the insurer's medical underwriting for
the amount above what's guaranteed.

Two-level shape (mirrors the insurer's own workflow):

- ``UnderwritingReview`` — ONE per (life, insurer, policy year). Carries the
  broker↔insurer workflow status (pending requirements → … → completed) and
  the medical-requirements notes. The "case" a broker opens with AIA covers
  every AIA product the life triggered on.
- ``UnderwritingCase`` — one line per triggered product under a review:
  requested (eligible) SI, guaranteed SI (auto-covered while the insurer
  decides), the insurer's decision, and the accepted amount.

Guaranteed SI at open: SI-trigger → max(FCL, last covered SI); age-trigger →
0 for a new hire, the last covered SI for an existing life. "Last covered"
comes from the previous benefit year's in-force amount for the same person.

Insurer listings read the lines as:
    Last Accepted Sum Insured = accepted (decided) / guaranteed (pending)
    Sum Insured Pending U/W   = eligible - guaranteed while undecided, else 0

``refresh_underwriting_cases`` keeps the set in sync with resolved coverage;
decisions are broker-recorded and audit-logged at the API layer.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid


class ReviewStatus:
    """Broker↔insurer workflow states for a review (the screenshot vocabulary)."""

    pending_requirements = "pending_requirements"  # Pending U/W Requirements
    pending_employee = "pending_employee"  # Pending Employee
    pending_insurer = "pending_insurer"  # Pending Insurer Decision
    pending_hr = "pending_hr"  # Pending HR
    completed = "completed"
    cancelled = "cancelled"


VALID_REVIEW_STATUSES = frozenset(
    {
        ReviewStatus.pending_requirements,
        ReviewStatus.pending_employee,
        ReviewStatus.pending_insurer,
        ReviewStatus.pending_hr,
        ReviewStatus.completed,
        ReviewStatus.cancelled,
    }
)

# Printable wording for the workflow states (the insurer's own vocabulary).
# Mirrored by ``frontend/src/api/underwriting.ts::REVIEW_STATUS_LABELS`` — keep
# the two in step so the queue screen and the exported report read alike.
REVIEW_STATUS_LABELS = {
    ReviewStatus.pending_requirements: "Pending U/W Requirements",
    ReviewStatus.pending_employee: "Pending Employee",
    ReviewStatus.pending_insurer: "Pending Insurer Decision",
    ReviewStatus.pending_hr: "Pending HR",
    ReviewStatus.completed: "Completed",
    ReviewStatus.cancelled: "Cancelled",
}


OPEN_REVIEW_STATUSES = frozenset(
    {
        ReviewStatus.pending_requirements,
        ReviewStatus.pending_employee,
        ReviewStatus.pending_insurer,
        ReviewStatus.pending_hr,
    }
)


class UnderwritingStatus:
    """Per-product decision on a case line."""

    pending = "pending"  # awaiting insurer decision; guaranteed SI in force
    approved_standard = "approved_standard"  # Approved Standard Life
    approved_substandard = "approved_substandard"  # Approved Substandard Life
    rejected = "rejected"  # excess refused; guaranteed SI stays in force
    postponed = "postponed"  # decision deferred — excess still pending
    closed = "closed"  # closed without a decision; guaranteed SI stays
    # Legacy vocabulary (pre insurer-grouped model) — normalized on read/write.
    accepted = "accepted"
    declined = "declined"


# Statuses whose amounts are final (nothing pending). ``postponed`` is NOT
# decided — the excess is still awaiting the insurer.
DECIDED_UW_STATUSES = frozenset(
    {
        UnderwritingStatus.approved_standard,
        UnderwritingStatus.approved_substandard,
        UnderwritingStatus.rejected,
        UnderwritingStatus.closed,
        UnderwritingStatus.accepted,
        UnderwritingStatus.declined,
    }
)

VALID_UW_STATUSES = frozenset(
    {
        UnderwritingStatus.pending,
        UnderwritingStatus.approved_standard,
        UnderwritingStatus.approved_substandard,
        UnderwritingStatus.rejected,
        UnderwritingStatus.postponed,
        UnderwritingStatus.closed,
    }
)

# Legacy → current decision vocabulary (rows written before the review model).
_LEGACY_STATUS_MAP = {
    UnderwritingStatus.accepted: UnderwritingStatus.approved_standard,
    UnderwritingStatus.declined: UnderwritingStatus.rejected,
}


# Printable wording for a per-product decision. Mirrors
# ``frontend/src/api/underwriting.ts::DECISION_LABELS``; legacy values are
# normalized before lookup, so they label as their current equivalent.
DECISION_LABELS = {
    UnderwritingStatus.pending: "Pending",
    UnderwritingStatus.approved_standard: "Approved Standard Life",
    UnderwritingStatus.approved_substandard: "Approved Substandard Life",
    UnderwritingStatus.rejected: "Rejected",
    UnderwritingStatus.postponed: "Postponed",
    UnderwritingStatus.closed: "Closed",
}


def normalize_uw_status(status: str) -> str:
    """Map legacy accepted/declined onto the current decision vocabulary."""
    return _LEGACY_STATUS_MAP.get(status, status)


class UnderwritingReview(Base, TimestampMixin):
    __tablename__ = "underwriting_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The insurer the review is opened with (``Product.insurer`` at sync time;
    # "" when the triggering product has no insurer assigned yet).
    insurer: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # Exactly one of employee_id / dependant_id is set (the underwritten life).
    employee_id: Mapped[str | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=True, index=True
    )
    dependant_id: Mapped[str | None] = mapped_column(
        ForeignKey("dependants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ReviewStatus.pending_requirements,
        index=True,
    )
    # Medical / evidence requirements the insurer asked for (free text).
    requirements: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class UnderwritingCase(Base, TimestampMixin):
    __tablename__ = "underwriting_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Parent review (nullable only for rows written before the review model —
    # the sync adopts them into a review on its next run).
    review_id: Mapped[str | None] = mapped_column(
        ForeignKey("underwriting_reviews.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
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
    # Requested amount — snapshot of the eligible SI at the last sync.
    eligible_si: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Auto-covered amount while the insurer decides (the NEL / last covered SI;
    # 0 for a new hire above the age gate). Broker-editable; the sync stops
    # recomputing it once ``guaranteed_overridden`` is set.
    guaranteed_si: Mapped[float | None] = mapped_column(Float, nullable=True)
    guaranteed_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Amount currently in force: guaranteed while pending, the insurer's figure
    # once decided. Never above eligible_si.
    accepted_si: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=UnderwritingStatus.pending, index=True
    )
    decided_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
