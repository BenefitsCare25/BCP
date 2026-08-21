"""Independent required-document and recognition setup for one claim scope."""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ClaimDocumentSetup(Base, TimestampMixin):
    __tablename__ = "claim_document_setups"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "claim_kind",
            "claim_key",
            "scope_code",
            name="uq_claim_document_setup_client_type_scope",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_label: Mapped[str] = mapped_column(String(128), nullable=False)
    # Ordered, defensive JSON. Each entry owns its upload slot and recognition
    # vocabulary; no document definition is shared with another claim scope.
    documents: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON(), nullable=True)
