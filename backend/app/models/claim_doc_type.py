"""Broker-configurable claim document types (aliases + key fields).

DB-backed successor to the in-code registry in
``services/claim_doc_types.py`` — the broker edits the ALIASES (alternate
titles hospitals print, e.g. "After Visit Summary" for a discharge summary)
and the KEY FIELDS (the completeness check: fields a genuine copy always
carries) on the claims page. Rows are PER CLIENT, lazily seeded from the
in-code defaults on first read; a client with no rows falls back to those
defaults, so intake classification and the review pipeline never depend on
the table being populated.

The classification *logic* (inpatient markers, govt/private disambiguation)
deliberately stays in code — only the vocabulary is config.
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ClaimDocType(Base, TimestampMixin):
    __tablename__ = "claim_doc_types"
    __table_args__ = (
        UniqueConstraint("client_id", "key", name="uq_claim_doc_type_client_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stable slug ("discharge_summary", "finalised_tax_invoice", or a
    # slugified custom name). Immutable after create — the seeded keys tie a
    # row back to its in-code default and, via slot_key, to upload slots.
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    display: Mapped[str] = mapped_column(String(128), nullable=False)
    # Alternate document titles (lowercased on write) that identify this type.
    aliases: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
    # [{"name": "Diagnosis", "keywords": ["diagnosis", "condition"]}, ...] —
    # keywords are the label-match tokens; empty keywords match on the name.
    key_fields: Mapped[list[dict] | None] = mapped_column(JSON(), nullable=True)
    # "govt" | "private" for the hospital-invoice pair; NULL = sector-neutral
    # (matched by alias alone, e.g. the discharge summary).
    sector: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Required-document upload slot this type fills when unambiguous
    # (claim_intake.DOC_SLOT_LABELS key) — drives autofill slot placement.
    slot_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
