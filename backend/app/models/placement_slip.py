"""Placement slip ingest record — points to the raw file and the parse log.

Categories produced by a slip are linked via `categories.source_ref`.
"""
from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ParseStatus(str, enum.Enum):
    pending = "pending"
    parsing = "parsing"
    parsed = "parsed"
    error = "error"


class PlacementSlipRow(Base, TimestampMixin):
    __tablename__ = "placement_slips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    blob_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus), nullable=False, default=ParseStatus.pending
    )
    parse_log: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
