"""Reusable, company-scoped employee-cohort matching profile."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class EligibilityMappingProfile(Base, TimestampMixin):
    """A confirmed or proposed category meaning reusable across policy years.

    ``category_signature`` removes plan-number/dependant boilerplate but remains
    company scoped. The compiled JSONLogic rule is copied onto ``Category`` for
    fast matching and historical snapshots; this row is the reusable source.
    """

    __tablename__ = "eligibility_mapping_profiles"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "category_signature",
            name="uq_eligibility_mapping_profile_client_signature",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_signature: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    matching_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    rule_human_readable: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    required_attributes: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    last_policy_year_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


__all__ = ["EligibilityMappingProfile"]
