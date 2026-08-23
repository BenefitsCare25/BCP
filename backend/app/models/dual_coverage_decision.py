"""What a broker decided about a dual-coverage case.

The DETECTION is computed on read (``services/dual_coverage``); this is the only
persisted part. One row per subject per benefit year.

Decisions are deliberately **per benefit year** and are re-asked at renewal:
coverage and premiums are re-placed annually, so the decision is re-made against
the new placement rather than inherited from a year whose roster has since
turned over.
"""
from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.migration_helpers import json_variant

# `carried_by` names the employee who keeps the life; `intentional_both` records
# deliberate dual cover; `not_a_match` dismisses a false positive — which
# name+DOB matching will occasionally produce, so the broker must be able to say
# "these are two different people" and have it stay said.
DECISIONS = ("carried_by", "intentional_both", "not_a_match", "dismissed")

SUBJECT_LIFE = "life"
SUBJECT_COUPLE = "couple"


class DualCoverageDecision(Base, TimestampMixin):
    __tablename__ = "dual_coverage_decisions"
    __table_args__ = (
        UniqueConstraint(
            "policy_year_id",
            "subject_key",
            name="uq_dual_coverage_decision_year_subject",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("clients.id", ondelete="CASCADE"), index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("policy_years.id", ondelete="CASCADE"), index=True
    )

    # "life" | "couple" — String(32) with the Python constant's value, never
    # sa.Enum (see the migrations note in CLAUDE.md).
    subject_kind: Mapped[str] = mapped_column(String(32), default=SUBJECT_LIFE)
    # OPAQUE sha256 prefix. Hashed because the readable form carries a full name
    # and date of birth, and this value rides in a URL path.
    subject_key: Mapped[str] = mapped_column(String(64), index=True)
    # EVERY candidate key the subject was reachable under when the decision was
    # taken. A decision matches a case if ANY key overlaps, which is what keeps
    # the decision attached through the workflow's own success: the broker fills
    # in the missing NRIC the case prompted them to fix, and a single-key scheme
    # would orphan the decision it just produced.
    subject_keys: Mapped[list[str] | None] = mapped_column(json_variant(), nullable=True)

    decision: Mapped[str] = mapped_column(String(32))
    carried_by_employee_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalized so the record stays readable after the FK is nulled by a
    # roster wipe — without it, "carried by <nobody>" is an unreadable decision.
    carried_by_staff_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )
    # Fingerprint of the family COMPOSITION when the decision was taken. A
    # mismatch marks the decision stale and the case re-surfaces — the failure
    # mode is always "ask again", never "silently resolved".
    parties_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
