"""No-login QR enrolment — tenant tables.

* ``PublicFormEntry`` — the shared company QR for one enrolment period. The
  token is looked up by its SHA-256; an encrypted copy lets an authorised
  broker download the SAME QR again (rotation replaces both).
* ``PublicIntake`` — one form an employee submitted through that QR. It is a
  claim of identity until a broker links it to a roster employee, which files
  it into the e-form register (``EnrollmentFormSubmission`` with source
  ``qr``). Never an election by itself.
* ``HrDownloadBatch`` — a frozen set of form PDFs packed into one private ZIP.
* ``HrDownloadGrant`` — one link + PIN for a batch. The first browser to tap
  Start owns it for exactly ``ACCESS_SECONDS``; reissuing revokes the previous
  grant of the batch.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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

INTAKE_PENDING = "pending"
INTAKE_LINKED = "linked"
INTAKE_REJECTED = "rejected"

BATCH_READY = "ready"
BATCH_CLOSED = "closed"

GRANT_REVOKE_REISSUED = "reissued"
GRANT_REVOKE_MANUAL = "manual"
GRANT_REVOKE_BATCH_CLOSED = "batch_closed"


class PublicFormEntry(Base, TimestampMixin):
    __tablename__ = "public_form_entries"
    __table_args__ = (
        UniqueConstraint("window_id", name="uq_public_form_entries_window"),
        UniqueConstraint("token_hash", name="uq_public_form_entries_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False
    )
    window_id: Mapped[str] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicIntake(Base, TimestampMixin):
    __tablename__ = "public_intakes"
    __table_args__ = (
        UniqueConstraint("client_id", "reference_no", name="uq_public_intakes_reference"),
        UniqueConstraint("entry_id", "request_id", name="uq_public_intakes_request"),
        Index("ix_public_intakes_window_status", "window_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False
    )
    window_id: Mapped[str | None] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="SET NULL"), nullable=True
    )
    entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # "QR-2026-00042" — printed on the PDF, quoted on the receipt, and carried
    # over unchanged to the register when a broker files the form.
    reference_no: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=INTAKE_PENDING)
    # The identity the visitor CLAIMED, kept as columns for matching only.
    claimed_staff_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claimed_national_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claimed_category_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Everything the PDF prints, frozen at submit.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON(), nullable=False, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    signer_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signer_user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # One download of the employee's own copy, from the submitting tab only.
    copy_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    copy_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    copy_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_employee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    linked_submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    linked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    rejected_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HrDownloadBatch(Base, TimestampMixin):
    __tablename__ = "hr_download_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False
    )
    window_id: Mapped[str | None] = mapped_column(
        ForeignKey("enrollment_windows.id", ondelete="SET NULL"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=BATCH_READY)
    # [{kind, id, reference_no, version, file_name, sha256, size_bytes, source, status}]
    manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON(), nullable=False, default=list)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    zip_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    zip_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class HrDownloadGrant(Base, TimestampMixin):
    __tablename__ = "hr_download_grants"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_hr_download_grants_token_hash"),
        Index("ix_hr_download_grants_batch", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("hr_download_batches.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Argon2id of the 6-digit PIN (core/passwords); the PIN itself is shown once.
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    pin_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recipient_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issued_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    unused_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # SHA-256 of the session nonce carried by the browser that tapped Start.
    session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    downloads: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_download_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
