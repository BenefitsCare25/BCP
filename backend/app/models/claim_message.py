"""One message on a conversation (tenant table).

**The table name under-describes it.** It holds the messages of claim threads
AND of member questions (`member_enquiries`), which is deliberate — the rules
below are written once, and a parallel `enquiry_messages` table would
re-implement every one of them. Renaming is not done: `op.rename_table` reaches
no per-firm Postgres schema (`scripts/provision_tenants.py` syncs new tables and
new columns only), so the name is cosmetic while the rename is a live-data risk.

**Exactly one of `claim_id` / `enquiry_id` is set**, and that is enforced in
`services/claim_messages._post`, not as a DB CHECK — `sync_firm_schema` does not
propagate constraints to firm schemas either, so a CHECK would hold in `public`
and nowhere else, which is worse than no constraint at all.

A claim carries ONE thread that both surfaces read and write:

    system  → posted automatically when the claim moves (submitted, approved,
              rejected, needs_info). Never written by hand.
    broker  → a broker typing on the claims queue.
    member  → the member replying from the portal.

**Every row is member-visible.** There is deliberately no "internal note" flag:
the broker already has `Claim.decision_notes` and the AI review for anything the
member must not see, and a thread where some rows are hidden is the shape that
eventually leaks one. If it is written here, it is addressed to the member.

Read state is TWO columns rather than one, because "read" is a fact about a
*recipient*, not about the row. A single `read_at` would have to mean "read by
the member" on a broker message and "read by a broker" on a member reply, and
the unread counts on the two surfaces would then be the same column read two
ways — which is how one side's badge silently clears the other's.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, new_uuid

AUTHOR_SYSTEM = "system"
AUTHOR_BROKER = "broker"
AUTHOR_MEMBER = "member"

# Claim lifecycle events that post a system message. These are the `event`
# values; the copy for each lives in services/claim_messages.py.
EVENT_SUBMITTED = "submitted"
EVENT_APPROVED = "approved"
EVENT_REJECTED = "rejected"
EVENT_NEEDS_INFO = "needs_info"

# NOTE: no AUTHOR_TYPES set and no MAX_BODY_CHARS here. Both existed, both were
# referenced by nothing, and the body caps that are actually ENFORCED live on
# the write schemas (`MemberMessageIn` 2000 / `BrokerMessageIn` 4000 in
# schemas/claims.py) — a second copy beside the column is free to drift from
# the limit that is applied, which is worse than not stating it.


class ClaimMessage(Base, TimestampMixin):
    __tablename__ = "claim_messages"
    __table_args__ = (
        # A thread read is (owner, chronological) on either side, and the
        # conversation projection groups and windows by the same key — so one
        # index per owner is what keeps that projection off a table scan.
        Index("ix_claim_messages_claim_created", "claim_id", "created_at"),
        Index("ix_claim_messages_enquiry_created", "enquiry_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Which thread this message belongs to — exactly one of the two, never both
    # and never neither. Nullable since questions arrived; every row written
    # before that carries a claim.
    claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enquiry_id: Mapped[str | None] = mapped_column(
        ForeignKey("member_enquiries.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    author_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Whichever identity applies; both NULL for a system message.
    author_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Display name SNAPSHOT. Resolved at write time and stored, so a disabled
    # broker account or a renamed member never blanks the name on an old
    # message — the thread is a record of what was said and by whom.
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The one-line headline the inbox lists ("Your claim is approved"). Always
    # populated — a message with no subject renders as an untitled row.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Set only on system messages (models.claim_message.EVENT_*).
    event: Mapped[str | None] = mapped_column(String(32), nullable=True)
    member_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    broker_read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
