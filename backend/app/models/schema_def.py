"""Employee attribute schema (Layer 2 — defines what an employee record looks like)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class EmployeeAttributeSchema(Base, TimestampMixin):
    __tablename__ = "employee_attribute_schemas"
    # One row per attribute per tenant. Global defaults (client_id NULL) are
    # exempt — SQL treats NULLs as distinct, which is the intended behaviour.
    __table_args__ = (
        UniqueConstraint("client_id", "attribute_id", name="uq_emp_attr_client_attribute"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="null = global Singapore default",
    )
    attribute_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    enum_values: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_pii: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # PII classification is a handling label, not an eligibility policy. A
    # personal field such as nationality can be evaluated inside our database
    # without ever exposing its values to an external model.
    allow_matching: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_ai_values: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    derived_from: Mapped[str | None] = mapped_column(String(64), nullable=True)
    derivation_rule: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
