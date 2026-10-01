"""Online enrolment e-form — per-period form setup + signed submission records.

``EnrollmentFormConfig`` holds the parts of the paper enrolment form that are NOT
on the placement slip: the wording (title, notes, declarations), the documents a
member must read (MAS guides, product summary, benefit schedule), and how each
product's premium is shared between company and employee. Products, plans,
family-tier rates and dependant age windows are never stored here — the form
reads them live from the policy setup, so it cannot drift from the slip.

``EnrollmentFormSubmission`` is one signed form: an immutable snapshot of what
the member saw and answered, the typed signature, a content hash and the
generated PDF. Resubmitting creates the next version and supersedes the last.
Paper forms a broker scans in are recorded here too (``source = "paper"``) so the
register is one list.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

FORM_SOURCE_PORTAL = "portal"
FORM_SOURCE_PAPER = "paper"

FORM_STATUS_SUBMITTED = "submitted"
FORM_STATUS_ACKNOWLEDGED = "acknowledged"
FORM_STATUS_SUPERSEDED = "superseded"


class EnrollmentFormConfig(Base, TimestampMixin):
    __tablename__ = "enrollment_form_configs"
    __table_args__ = (
        UniqueConstraint("window_id", name="uq_enrollment_form_config_window"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    window_id: Mapped[str] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="CASCADE"), nullable=False
    )
    # Validated JSON bag — see services/enrollment_forms/config.py for the shape.
    settings: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class EnrollmentFormSubmission(Base, TimestampMixin):
    __tablename__ = "enrollment_form_submissions"
    __table_args__ = (
        UniqueConstraint("client_id", "reference_no", name="uq_enrollment_form_reference"),
        # Backstop to the per-employee lock in submission.py (a NULL window —
        # paper forms filed outside a period — is serialised by the lock alone).
        UniqueConstraint(
            "employee_id", "window_id", "version", name="uq_enrollment_form_version"
        ),
        Index("ix_enrollment_form_submissions_window_status", "window_id", "status"),
        Index("ix_enrollment_form_submissions_employee", "employee_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    window_id: Mapped[str | None] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="SET NULL"), nullable=True
    )
    enrollment_id: Mapped[str | None] = mapped_column(
        ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False
    )
    # Human reference printed on the PDF and quoted to the helpline ("EF-2026-00042").
    reference_no: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FORM_SOURCE_PORTAL, server_default=FORM_SOURCE_PORTAL
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FORM_STATUS_SUBMITTED,
        server_default=FORM_STATUS_SUBMITTED,
    )
    # Everything the PDF prints, frozen at submit. Empty for paper uploads.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    signature_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signer_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signer_user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # SHA-256 of the canonical snapshot JSON — printed on the PDF so a printed
    # copy can be checked against the record.
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The PDF (generated, or the scanned paper form) — a StoredDocument row.
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    broker_note: Mapped[str | None] = mapped_column(Text, nullable=True)
