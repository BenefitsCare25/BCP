"""Server-side working copy for the member claim form."""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ClaimFormDraft(Base, TimestampMixin):
    __tablename__ = "claim_form_drafts"
    __table_args__ = (
        Index(
            "uq_claim_form_drafts_employee_year",
            "employee_id",
            "policy_year_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    form_data: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
