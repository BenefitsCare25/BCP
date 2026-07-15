"""Insurance/flex claim submitted by a portal member (tenant table).

Status machine (single source of truth: `VALID_TRANSITIONS`):

    draft ──submit──▶ submitted ──pipeline──▶ ai_review_pending ─▶ ai_verified
                          │                        │               ai_flagged
                          │  (pipeline error: claim returns to "submitted",
                          │   the review row records the failure)
                          ▼
    broker decision (from submitted / ai_* / needs_info):
        approve → approved (terminal)   reject → rejected (terminal)
        needs_info → member edits + resubmits → submitted
    draft → member delete (row removed)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

CLAIM_STATUS_DRAFT = "draft"
CLAIM_STATUS_SUBMITTED = "submitted"
CLAIM_STATUS_AI_REVIEW_PENDING = "ai_review_pending"
CLAIM_STATUS_AI_VERIFIED = "ai_verified"
CLAIM_STATUS_AI_FLAGGED = "ai_flagged"
CLAIM_STATUS_NEEDS_INFO = "needs_info"
CLAIM_STATUS_APPROVED = "approved"
CLAIM_STATUS_REJECTED = "rejected"

CLAIM_STATUSES = frozenset(
    {
        CLAIM_STATUS_DRAFT,
        CLAIM_STATUS_SUBMITTED,
        CLAIM_STATUS_AI_REVIEW_PENDING,
        CLAIM_STATUS_AI_VERIFIED,
        CLAIM_STATUS_AI_FLAGGED,
        CLAIM_STATUS_NEEDS_INFO,
        CLAIM_STATUS_APPROVED,
        CLAIM_STATUS_REJECTED,
    }
)

# States a broker may decide from (approve / reject / needs_info).
DECIDABLE_STATUSES = frozenset(
    {
        CLAIM_STATUS_SUBMITTED,
        CLAIM_STATUS_AI_REVIEW_PENDING,
        CLAIM_STATUS_AI_VERIFIED,
        CLAIM_STATUS_AI_FLAGGED,
        CLAIM_STATUS_NEEDS_INFO,
    }
)

# States in which the member may still edit / add documents / submit.
MEMBER_EDITABLE_STATUSES = frozenset({CLAIM_STATUS_DRAFT, CLAIM_STATUS_NEEDS_INFO})

# States that count against limits/duplicate checks ("live" claims).
LIVE_STATUSES = frozenset(CLAIM_STATUSES - {CLAIM_STATUS_DRAFT, CLAIM_STATUS_REJECTED})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    CLAIM_STATUS_DRAFT: frozenset({CLAIM_STATUS_SUBMITTED}),
    CLAIM_STATUS_SUBMITTED: frozenset(
        {
            CLAIM_STATUS_AI_REVIEW_PENDING,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
        }
    ),
    CLAIM_STATUS_AI_REVIEW_PENDING: frozenset(
        {
            CLAIM_STATUS_AI_VERIFIED,
            CLAIM_STATUS_AI_FLAGGED,
            # Pipeline failure falls back to plain "submitted" (manual review).
            CLAIM_STATUS_SUBMITTED,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            # Self-transition: broker rerun-review recovers a claim whose
            # background task died before persisting anything (BackgroundTasks
            # are in-process and non-durable — a deploy/crash in the window
            # after submit strands the claim here otherwise).
            CLAIM_STATUS_AI_REVIEW_PENDING,
        }
    ),
    CLAIM_STATUS_AI_VERIFIED: frozenset(
        {
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            # Broker rerun-review re-enters the pipeline.
            CLAIM_STATUS_AI_REVIEW_PENDING,
        }
    ),
    CLAIM_STATUS_AI_FLAGGED: frozenset(
        {
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            CLAIM_STATUS_AI_REVIEW_PENDING,
        }
    ),
    CLAIM_STATUS_NEEDS_INFO: frozenset(
        {
            CLAIM_STATUS_SUBMITTED,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
        }
    ),
    CLAIM_STATUS_APPROVED: frozenset(),
    CLAIM_STATUS_REJECTED: frozenset(),
}

CLAIM_KIND_INSURED = "insured"
CLAIM_KIND_FLEX = "flex"


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"
    __table_args__ = (
        Index(
            "ix_claims_employee_year_status",
            "employee_id",
            "policy_year_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant when claiming for a covered dependant; NULL = the member.
    dependant_id: Mapped[str | None] = mapped_column(
        ForeignKey("dependants.id", ondelete="SET NULL"), nullable=True
    )
    claim_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CLAIM_KIND_INSURED
    )
    # Insured claims: which coverage line + SOB benefit item the claim draws on.
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    benefit_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Flex claims: which claimable scheme category.
    flex_category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    incurred_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Receipt / tax-invoice number the member transcribes — cross-checked against
    # the uploaded documents by the AI review (see field_maps.py).
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Free-text member note (not a document-matched field).
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_claimed: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="SGD")
    amount_converted: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_approved: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CLAIM_STATUS_DRAFT, index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Member-entered claim-form snapshot the AI review compares documents against.
    form_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
