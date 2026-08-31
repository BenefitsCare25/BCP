"""Reviewed employee-listing column mappings, remembered per company/template."""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class RosterMappingProfile(Base, TimestampMixin):
    """The exact source-column decisions for one stable workbook shape.

    Mapping keys are zero-based column indexes rather than header strings. That
    keeps duplicate headings unambiguous; the fingerprint binds the decisions
    to the ordered, normalized header sequence so they cannot be reused on a
    different layout.
    """

    __tablename__ = "roster_mapping_profiles"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "member_type",
            "fingerprint",
            name="uq_roster_mapping_profile_client_type_fingerprint",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    member_type: Mapped[str] = mapped_column(
        String(24), nullable=False, default="employee"
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_headers: Mapped[list[str]] = mapped_column(JSON(), nullable=False)
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
