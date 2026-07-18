"""Plan — Layer 3 instance data storing parsed Schedule of Benefits per plan code."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "policy_year_id", "code",
            name="uq_plan_product_year_code",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    benefit_schedule: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    cover_description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    annual_policy_limit: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Insurer-facing label for report columns ("4 Bed Restr Hosp / Inpatient
    # Expenses - S$10,000", "Panel Only") — internal names stay "Plan 1".
    # Operational metadata: editable on active years (not activation-locked).
    report_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False, default="system_generated")
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="needs_review", index=True
    )
    human_modified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
