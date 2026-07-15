"""Per-client BYOK (Bring Your Own Key) AI provider configuration.

1:1 with `clients`. The API key is stored encrypted under the master key in
``app/core/crypto.py``; the cleartext never leaves the backend.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid

if TYPE_CHECKING:
    from app.models.client import Client


class ClientAIConfig(Base, TimestampMixin):
    __tablename__ = "client_ai_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    encrypted_api_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # First 16 hex chars of sha256(plaintext) — non-reversible change marker.
    key_fingerprint: Mapped[str] = mapped_column(String(16), nullable=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_validation_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    client: Mapped[Client] = relationship(back_populates="ai_config")
