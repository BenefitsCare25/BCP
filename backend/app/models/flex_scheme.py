"""FlexScheme — a resumable draft of a Flexible-Benefits configuration.

Flex (flexible benefits) is a reimbursement / spending-account benefit, not an
insured product: each eligible employee gets a monetary wallet whose size depends
on their family status, plus a set of claimable benefit categories with co-share
rules. The shape varies wildly across companies/countries (family-status-tiered
vs flat system cap, different currencies, different sub-limits), so a broker
uploads a heterogeneous document, AI extracts the four parameter groups into one
normalized ``scheme`` bag, the broker reviews/edits it, then confirms.

Unlike ``ProductSetup``, confirm does NOT materialize catalog rows or run matching
(that is a later phase) — it only validates and flips ``status`` to ``confirmed``.
One scheme per policy year; it holds multiple eligibility tiers.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class FlexSchemeStatus:
    draft = "draft"
    confirmed = "confirmed"


class FlexSchemeOrigin:
    """How the scheme's initial answers were produced.

    ``manual`` — the broker authored the scheme from scratch.
    ``upload`` — the answers were extracted by AI from an uploaded document.
    """

    manual = "manual"
    upload = "upload"


class FlexScheme(Base, TimestampMixin):
    __tablename__ = "flex_schemes"
    # One Flex scheme per policy year — makes upsert-by-year natural; the scheme
    # itself holds the multiple eligibility tiers.
    __table_args__ = (
        UniqueConstraint("policy_year_id", name="uq_flex_scheme_year"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FlexSchemeStatus.draft,
        server_default=FlexSchemeStatus.draft, index=True,
    )
    # The full editable bag: {"meta": {...}, "tiers": [...], "dependant_def": {...}}.
    # Shape mirrors the AI extraction tool output (minus confidence/reasoning,
    # which are promoted to columns). See app/services/ai_extractor.py.
    scheme: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FlexSchemeOrigin.manual,
        server_default=FlexSchemeOrigin.manual,
    )
    # Source document filename (provenance for upload-origin schemes).
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # AI extraction confidence (0..1), null for manually authored schemes.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
