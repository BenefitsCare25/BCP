"""ProductSetup — a resumable draft of the guided product-setup form.

Holds the broker's in-progress answers for one product within a policy year.
The draft is the editable source of truth; on confirm it materializes into the
catalog `Product` + per-plan `Plan` rows (with `source="manual"`), the same
shape the placement-slip parser produces. Re-confirming re-projects the draft.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ProductSetupStatus:
    draft = "draft"
    confirmed = "confirmed"


class ProductSetupOrigin:
    """How the draft's initial answers were produced.

    ``manual`` — the broker started from the blank template defaults.
    ``placement_slip`` — the answers were pre-filled from a parsed slip; on
    confirm such a draft supersedes the provisional ``system_generated`` rows
    the upload created for the same product (so there are no duplicates).
    """

    manual = "manual"
    placement_slip = "placement_slip"


class ProductSetup(Base, TimestampMixin):
    __tablename__ = "product_setups"
    # One setup per product code per policy year — makes upsert-by-code natural.
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "product_code", name="uq_product_setup_year_code"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[int] = mapped_column(nullable=False, default=1)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProductSetupStatus.draft, index=True
    )
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ProductSetupOrigin.manual,
        server_default=ProductSetupOrigin.manual,
    )
    # For slip-derived drafts: the placement_slips row the answers came from.
    origin_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    materialized_product_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
