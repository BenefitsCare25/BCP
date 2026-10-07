"""A company's own Employee Listing: its layout and each person's listed cover.

``ElLayoutProfile`` remembers, per company, how its listing workbook is laid
out (which column plays which role, which block is which product) and how the
listing's category wording maps onto the placement-slip categories. The Full
Employee Listing export reproduces the same layout from it, and the next
upload reuses the reviewed mapping.

``ListingAssignment`` records what the uploaded listing states for one person
(employee or dependant) under one product: the category and plan wording, the
family tier, the administration type and the recorded figures. Matching uses
the mapped category for people already on the listing; the rule grid places
joiners added later.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ElLayoutProfile(Base, TimestampMixin):
    __tablename__ = "el_layout_profiles"
    __table_args__ = (UniqueConstraint("client_id", name="uq_el_layout_profile_client"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Serialized ElLayout: banner/header rows, blocks with column roles, trailing.
    layout: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    # Block index (str) -> product codes the block reports (base + variants).
    block_products: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    # Block index (str) -> {normalized listing label -> {"product_code",
    # "category_signature", "label"}} as reviewed by the broker.
    label_map: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    source_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ListingAssignment(Base, TimestampMixin):
    __tablename__ = "listing_assignments"
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id", "product_id", "member_key",
            name="uq_listing_assignment_member_product",
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
    dependant_id: Mapped[str | None] = mapped_column(
        ForeignKey("dependants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # "E:<employee id>" or "D:<dependant id>" — one listed cover per person and
    # product (a nullable dependant_id cannot carry that uniqueness itself).
    member_key: Mapped[str] = mapped_column(String(48), nullable=False)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    listed_category: Mapped[str | None] = mapped_column(String(512), nullable=True)
    listed_plan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_group: Mapped[str | None] = mapped_column(String(2), nullable=True)
    admin_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Other recorded cells by role (sums insured, underwriting state, premium
    # as listed). The listing's premium is evidence, never the price.
    recorded: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
