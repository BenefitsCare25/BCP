"""FlexPricing — per-policy-year "price tag" matrix for flex-funded enrollment.

A member's Flexible-Benefits wallet (see ``flex_scheme`` / ``Employee.flex_*``) can
be spent to offset the cost of the insured coverage they elect during enrollment.
The amount deducted from the wallet is the **price tag** — deliberately distinct
from the **premium** (the insurer's group rate, parsed from the placement slip into
``Category.plan_assignments``). The broker sets price tags; the insurer sets premiums.

Price tags are NOT per employee. They vary by:

- **electable tier** — the same ``(tier_category_id, plan_code)`` identity the
  enrollment options use (so a member's upgrade/downgrade choices each carry their
  own price), and
- **age band** — defined PER PRODUCT (premium age-banding differs by product).

The whole matrix is one editable JSON bag per policy year (upsert, like
``LeavePolicy``); the resolved price tag is snapshotted onto the election /
override at confirm so reporting stays stable if the matrix later changes.

Shape::

    {
      "products": {
        "<product_id>": {
          "age_bands": [{"label": "<30", "min": 0, "max": 29}, ...],
          "price_tags": {
            "<tier_category_id>::<plan_code>": {"<30": 1200.0, "30-39": 1500.0}
          }
        }
      }
    }
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class FlexPricing(Base, TimestampMixin):
    __tablename__ = "flex_pricing"
    # One pricing matrix per policy year — upsert-by-year, like LeavePolicy.
    __table_args__ = (
        UniqueConstraint("policy_year_id", name="uq_flex_pricing_year"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # {"products": {product_id: {"age_bands": [...], "price_tags": {...}}}}.
    pricing: Mapped[dict[str, Any]] = mapped_column(
        JSON(), nullable=False, default=dict, server_default=text("'{}'")
    )
