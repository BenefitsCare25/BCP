"""Retained uploaded document metadata (tenant table).

The bytes live in the storage backend (`app/core/storage.py`); this row is
the queryable record: which entity it belongs to, its SHA-256 (duplicate /
tampering detection), and who uploaded it (member or broker user).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

DOC_ENTITY_CLAIM = "claim"
DOC_ENTITY_DEPENDANT = "dependant"
# Member-level referral letters (entity_id = the member's Employee row id) —
# reusable across specialist claims via Claim.referral_document_id.
DOC_ENTITY_REFERRAL = "referral"
STORAGE_AVAILABLE = "available"
STORAGE_DELETE_PENDING = "delete_pending"


class StoredDocument(Base, TimestampMixin):
    __tablename__ = "stored_documents"
    __table_args__ = (
        CheckConstraint(
            "storage_state IN ('available', 'delete_pending')",
            name="storage_state_valid",
        ),
        Index("ix_stored_documents_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Which required-document slot a claim upload fills (claim_intake.DOC_SLOT
    # keys, e.g. "itemised_tax_invoice"); NULL = untagged/additional document.
    doc_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The date the DOCUMENT ITSELF states it was issued — distinct from
    # `created_at`, which is when it was uploaded. Only the referral-letter flow
    # asks for it today, and only referral letters need it: a referral has a
    # validity period an insurer measures from its issue date, and the upload
    # date is not a proxy for it (a member scans a six-month-old letter the day
    # they first claim). NULL everywhere else, and NULL on a referral whose date
    # the member did not supply — the age rule skips rather than guesses.
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=STORAGE_AVAILABLE,
        server_default=STORAGE_AVAILABLE,
    )
    delete_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
