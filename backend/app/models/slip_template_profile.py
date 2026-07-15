"""Per-tenant memory of a placement-slip template's SOB column layout.

When the deterministic profiler mis-reads an unfamiliar carrier template, the
broker corrects the column->role mapping once. We store that correction keyed by
a stable template fingerprint (product + insurer + Schedule-of-Benefits header
signature) so every later upload of the same template reuses it — the profiler
is overridden before it runs.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class SlipTemplateProfile(Base, TimestampMixin):
    __tablename__ = "slip_template_profiles"
    # One stored mapping per (tenant, template fingerprint). Re-saving a
    # correction for the same template updates the existing row.
    __table_args__ = (
        UniqueConstraint(
            "client_id", "fingerprint", name="uq_slip_template_profile_client_fingerprint"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stable signature of the template's SOB layout (see slip_template_memory).
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    insurer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Human-friendly label for the override (sheet name) shown in the UI.
    sheet_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The corrected column->role mapping: {name_col, key_col, value_col,
    # allow_letter_keys, name_first}. Consumed by the parser as a roles override.
    roles: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
