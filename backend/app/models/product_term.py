"""ProductTerm — per-product terms (coverage period + GST) within a policy year.

A policy year is the config-version container and carries the client's nominal
coverage window (`PolicyYear.start_date` / `end_date`). Individual products
(Life, Medical, Dental, …) often renew on different cycles, so each product may
override that window with its own coverage period. Products also carry the GST
option: slip-extracted premiums are GST-exclusive, and `gst_included` grosses
up premium displays/computations by `gst_rate`.

Storage is sparse: a row exists ONLY for products with an explicit setting.
Null coverage dates inherit the policy year's span (a row may exist for GST
alone), so existing single-period setups keep working with no backfill. The
keyed product is the catalog `Product` (per-policy-year identity is
`(policy_year_id, product_id)`).

`gst_included` is a TRI-STATE (nullable) so a product-level opinion is
distinguishable from "unset": None = no opinion (flex tags inherit the
flex-scheme GST default; the insurance premium is not grossed), True = gross
by `gst_rate`, False = explicit "no GST" (overrides the scheme default). This
keeps the two dimensions (coverage dates, GST) independent — setting a coverage
period does not assert a GST opinion.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

# Singapore GST (%) — the default when a product enables GST without an
# explicit rate. Stored raw amounts stay exclusive; the rate only grosses up.
DEFAULT_GST_RATE = 9.0


class ProductTerm(Base, TimestampMixin):
    __tablename__ = "product_terms"
    # One terms override per product per policy year.
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "product_id", name="uq_product_term_year_product"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    coverage_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    coverage_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Tri-state: None = inherit (no opinion), True = gross, False = explicit off.
    gst_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # GST percentage (0-100); None falls back to DEFAULT_GST_RATE when included.
    gst_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Free cover limit: sum insured auto-accepted without medical underwriting.
    # None = no FCL (everything auto-accepted). Drives underwriting-case sync.
    free_cover_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
