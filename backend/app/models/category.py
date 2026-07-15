"""Category — the central record with full provenance envelope (brief §3.3)."""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import JSON, Base, TimestampMixin, new_uuid

if TYPE_CHECKING:
    from app.models.policy_year import PolicyYear


class SourceKind(str, enum.Enum):
    manual = "manual"  # human typed it
    system_generated = "system_generated"  # deterministic parser / rule generator
    ai_extracted = "ai_extracted"  # LLM (Azure Foundry)
    csv_import = "csv_import"  # bulk-imported from a structured file


class CategoryStatus(str, enum.Enum):
    draft = "draft"
    needs_review = "needs_review"
    confirmed = "confirmed"


class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), nullable=True, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_description: Mapped[str] = mapped_column(String(2048), nullable=False)
    matching_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    rule_human_readable: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    participation_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Structured reading of the slip Participation cell: employee/dependant modes
    # plus the allowed voluntary change direction (upgrade/downgrade/both). Shape:
    # {"employee": ..., "dependant": ..., "direction": ..., "raw": ...}. Drives
    # cohort-scoped, direction-aware enrollment elections (see services/cohort_tiers).
    participation_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    plan_assignments: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)

    # Provenance envelope — brief §3.3.
    # Stored as String (not Enum) so adding new source/status values doesn't
    # require dropping a SQLite CHECK constraint. SourceKind / CategoryStatus
    # are still used in Python for type-safety and exhaustiveness.
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SourceKind.system_generated.value
    )
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CategoryStatus.needs_review.value,
        index=True,
    )
    human_modified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    modified_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    policy_year: Mapped[PolicyYear] = relationship(back_populates="categories")
