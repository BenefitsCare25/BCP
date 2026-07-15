"""Retained uploaded document metadata (tenant table).

The bytes live in the storage backend (`app/core/storage.py`); this row is
the queryable record: which entity it belongs to, its SHA-256 (duplicate /
tampering detection), and who uploaded it (member or broker user).
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

DOC_ENTITY_CLAIM = "claim"
DOC_ENTITY_DEPENDANT = "dependant"


class StoredDocument(Base, TimestampMixin):
    __tablename__ = "stored_documents"
    __table_args__ = (
        Index("ix_stored_documents_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    uploaded_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
