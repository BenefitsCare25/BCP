"""Product catalog + per-product plan attribute schemas (Layer 2)."""
from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import JSON, Base, TimestampMixin, new_uuid
from app.services.insurance_lines import InsuranceLine, infer_line


class ParticipationModel(str, enum.Enum):
    standard = "standard"
    extended = "extended"
    eo_only = "eo_only"


class Product(Base, TimestampMixin):
    __tablename__ = "products"
    # One row per product code per tenant. Global catalog rows (client_id NULL)
    # are exempt — SQL treats NULLs as distinct.
    __table_args__ = (
        UniqueConstraint("client_id", "code", name="uq_product_client_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    insurer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    participation_model: Mapped[ParticipationModel] = mapped_column(
        Enum(ParticipationModel), nullable=False, default=ParticipationModel.standard
    )
    has_dependants: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_outpatient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    product_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)

    plan_schemas: Mapped[list[PlanAttributeSchema]] = relationship(back_populates="product")

    @property
    def line(self) -> InsuranceLine:
        """Broker-facing Medical / Life / Flex line — a `product_metadata['line']`
        override wins, else inferred from the code. Computed, not stored."""
        meta = self.product_metadata if isinstance(self.product_metadata, dict) else {}
        return infer_line(self.code, meta.get("line"))

    @property
    def flex_pricing_mode(self) -> str:
        """Default flex price-tag config shape: ``age_banded`` (premiums rise with
        age — life products) or ``plan_type`` (one price per plan). Derived from the
        insurance line so it's data-driven, not a hardcoded code list. A per-policy-
        year override can still flip an individual product (see ``flex_pricing``)."""
        return "age_banded" if self.line == "life" else "plan_type"


class PlanAttributeSchema(Base, TimestampMixin):
    __tablename__ = "plan_attribute_schemas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attribute_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    product: Mapped[Product] = relationship(back_populates="plan_schemas")
