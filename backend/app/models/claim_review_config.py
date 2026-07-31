"""Per-claim-type AI review rule setup (tenant table).

One row per (client, claim type) — the claim type is the same identity the
member claim form derives: ``claim_kind`` "insured" + a product code, or
"flex" + a flex benefit-category name. The row carries the three configurable
dimensions of the AI claim review (see ``services/claim_review_configs.py``):

- ``field_maps``          — claim-form ↔ document field pairs with a match
  mode (fuzzy/exact/numeric+tolerance) and a vision re-check flag.
- ``ai_rules``            — free-text business rules the AI judges, each with
  a category and a severity (critical|warning|info). Only a CRITICAL failure
  can flag the claim; warning/info failures surface without auto-flagging.
- ``required_documents``  — document families the review checks for presence.
  Empty/NULL keeps the automatic slot/sub-type derivation in
  ``claims_review/field_maps.py``.

A claim type with NO row (or a disabled one) uses the in-code defaults, so
the review never depends on config existing. Deliberately NOT lazily seeded —
absence of a row IS the default, and the UI shows a "Default" badge instead.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid


class ClaimReviewConfig(Base, TimestampMixin):
    __tablename__ = "claim_review_configs"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "claim_kind", "claim_key",
            name="uq_claim_review_config_client_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "insured" | "flex" (models/claim.py CLAIM_KIND_*).
    claim_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Product code for insured claims; the flex category NAME for flex claims
    # (matched casefolded — a renamed flex category simply stops matching and
    # that claim type reverts to the defaults).
    claim_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # Human label shown in lists and the cross-company import dialog.
    display_label: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # [{"portal_field", "document_field", "mode", "tolerance"?,
    #   "verify_with_vision"}] — read defensively in config_from_row.
    field_maps: Mapped[list[dict] | None] = mapped_column(JSON(), nullable=True)
    # [{"id", "rule", "category", "severity"}]
    ai_rules: Mapped[list[dict] | None] = mapped_column(JSON(), nullable=True)
    # ["receipt or tax invoice", ...]; empty/NULL = automatic derivation.
    required_documents: Mapped[list[str] | None] = mapped_column(JSON(), nullable=True)
