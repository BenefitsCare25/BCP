"""A broker's decision on one dependant's cover under one product.

A dependant above the product's spouse or child age limit is flagged, never
removed automatically: insurers often extend child cover for full-time
students or disabled children, so only the broker knows whether the cover
stands. ``keep`` records that it does (with the reason); ``exclude`` takes the
dependant out of that product's cover everywhere it is worked out.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

DECISION_KEEP = "keep"
DECISION_EXCLUDE = "exclude"
DECISIONS = (DECISION_KEEP, DECISION_EXCLUDE)


class DependantCoverDecision(Base, TimestampMixin):
    __tablename__ = "dependant_cover_decisions"
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "dependant_id", "product_id",
            name="uq_dependant_cover_decision",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dependant_id: Mapped[str] = mapped_column(
        ForeignKey("dependants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
