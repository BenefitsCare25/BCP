"""AI review of one submitted claim (tenant table).

One row per pipeline run. A rerun supersedes the previous row (``superseded``
flag) rather than mutating it, so the broker can always see what the AI said
at decision time. JSON payload shapes follow the IVM review pipeline:

- ``extractions``  — per-document ``{document_id, file_name, document_type,
  fields:[{id,label,value,field_type,confidence,page_number,raw_text}]}``
- ``field_comparisons`` — ``[{field_name, claim_value, document_value, status
  (MATCH|MISMATCH|MISSING_IN_PDF|MISSING_ON_PAGE|UNCERTAIN), confidence,
  notes, vision_verified}]``
- ``rule_results`` — ``[{rule, status (pass|fail|warning), source
  (deterministic|ai), evidence}]``
- ``vision_checks`` — ``[{question, field_name, verdict (CONFIRMED|REFUTED|
  UNCERTAIN), explanation, document_id}]``
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_COMPLETE = "complete"
REVIEW_STATUS_ERROR = "error"

REVIEW_VERDICT_CLEAN = "clean"
REVIEW_VERDICT_FLAGGED = "flagged"


class ClaimAIReview(Base, TimestampMixin):
    __tablename__ = "claim_ai_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=REVIEW_STATUS_PENDING
    )
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    extractions: Mapped[list[Any] | None] = mapped_column(JSON(), nullable=True)
    field_comparisons: Mapped[list[Any] | None] = mapped_column(JSON(), nullable=True)
    rule_results: Mapped[list[Any] | None] = mapped_column(JSON(), nullable=True)
    vision_checks: Mapped[list[Any] | None] = mapped_column(JSON(), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
