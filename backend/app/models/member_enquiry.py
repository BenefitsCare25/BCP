"""A member's question that hangs off no claim (tenant table).

The thread HEADER. Its messages live in `claim_messages` alongside every claim
thread's, which is deliberate: the five rules that make a conversation correct —
the member never learns which broker replied, a system notice is a record rather
than a template, posting never commits, `created_at` is stamped explicitly
because `server_default=now()` cannot order a thread, and "read" is two columns
because it is a fact about a *recipient* — are all written once, in
`services/claim_messages.py`. A parallel `enquiry_messages` table would
re-implement every one of them and give the conversation queries two shapes to
UNION on every read.

The cost of that choice is the NAME `claim_messages`, which now under-describes
what it holds. Renaming it is deliberately not done: `op.rename_table` reaches
no per-firm Postgres schema (`scripts/provision_tenants.py` syncs new tables and
new columns only), so the name is cosmetic while the rename is a live-data risk.

**`about_claim_id` is a REFERENCE, not ownership.** A question that is genuinely
about one claim is answered ON that claim — the portal routes the member there,
because a second thread tagged to a claim is two conversations about one thing,
each able to be read while the other still shows unread. This column exists for
the case routing cannot serve: a question that NAMES a claim without belonging
to it ("why was my June one settled at less than I paid?"). It is spelled
differently from `claim_messages.claim_id` on purpose — that column decides which
thread a message is in, this one decides nothing at all.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, new_uuid

if TYPE_CHECKING:
    from app.models.claim import Claim

STATUS_OPEN = "open"
STATUS_ANSWERED = "answered"
STATUS_CLOSED = "closed"

ENQUIRY_STATUSES = frozenset({STATUS_OPEN, STATUS_ANSWERED, STATUS_CLOSED})

# How many questions AWAITING A REPLY one member may hold. Without a cap this is
# a free-text sink attached to a queue nobody is paged for (prod cannot send
# email at all — see docs/EMAIL_SETUP.md).
#
# "Open" here means `status == open`, i.e. waiting on us — NOT "not closed".
# Counting `answered` threads made the cap unclearable by the member: the first
# broker reply marks a thread answered and only a broker may close one, so five
# answered questions refused the sixth forever. See `open_enquiry_count`.
MAX_OPEN_ENQUIRIES = 5

# The topic vocabulary, in the order the picker shows it: (key, label, routes,
# urgent).
#
# It lives HERE — on a leaf module — rather than beside the picker, because two
# places need it and one of them cannot import the other: `services/
# member_enquiries.py` builds the picker from it, and `services/
# claim_messages.py` labels and ORDERS a conversation row with it, while
# member_enquiries already imports claim_messages. A raw key on a list row is
# the alternative, and it shows: the broker's queue printed `Question · clinics
# · answered`, and the member's row dropped the topic altogether so every
# question they had ever asked read `Question`.
#
# `claim` is the ROUTING option: it creates nothing, it sends the member to the
# claim's own thread. `assert_topic_storable` refuses it as a stored value, so
# it can never appear as a row's label.
#
# `log_request` is the URGENT one. A Letter of Guarantee is what a hospital
# wants before it admits somebody, so a member asking for one is usually
# standing at an admissions counter — it is the only topic where the delay IS
# the harm. Urgency is a BROKER-side fact: it lifts the thread to the top of the
# queue and marks it there, and it is deliberately not dressed up on the
# member's own screen, because the portal promises no turnaround anywhere (prod
# cannot email them when we reply) and a badge saying "urgent" to the person
# waiting is a promise in everything but wording.
#
# The label carries the acronym in parentheses after the plain words, per the
# Printed-Label Rule: a member has never been told what LOG stands for, and a
# broker calls it nothing else — so both sides can say the same thing.
ENQUIRY_TOPICS: tuple[tuple[str, str, bool, bool], ...] = (
    ("claim", "About a claim I've sent", True, False),
    ("log_request", "Letter of Guarantee (LOG)", False, True),
    ("coverage", "Coverage & benefits", False, False),
    ("family", "My family", False, False),
    ("clinics", "Clinics & cards", False, False),
    ("enrolment", "Enrolment & plan changes", False, False),
    ("other", "Something else", False, False),
)

_TOPIC_LABELS = {key: label for key, label, _, _ in ENQUIRY_TOPICS}

# Non-empty by construction; `claim_messages` builds an IN() from it.
URGENT_TOPICS: frozenset[str] = frozenset(
    key for key, _, _, urgent in ENQUIRY_TOPICS if urgent
)


def topic_label(topic: str | None) -> str | None:
    """The member-facing name of a topic. An unrecognised key is returned as
    itself rather than dropped — a row whose topic we cannot name must still say
    what it was filed under."""
    if not topic:
        return None
    return _TOPIC_LABELS.get(topic, topic)


def topic_is_urgent(topic: str | None) -> bool:
    return bool(topic) and topic in URGENT_TOPICS


class MemberEnquiry(Base, TimestampMixin):
    __tablename__ = "member_enquiries"
    __table_args__ = (
        # The member's own list, and the broker's queue.
        Index("ix_member_enquiries_employee_year", "employee_id", "policy_year_id"),
        Index("ix_member_enquiries_client_status", "client_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # THE SCOPE, and deliberately the Employee rather than the MemberAccount:
    # every other member surface resolves through `resolve_member_employee`, and
    # an Employee row is per policy year, which is the granularity a question
    # about this year's benefits belongs to.
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A label and a routing aid for the broker's queue. NEVER a permission.
    topic: Mapped[str] = mapped_column(String(32), nullable=False)
    # The member's own words, and the thread's title in both lists.
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_OPEN, server_default=STATUS_OPEN
    )
    # Context only — see the module docstring. SET NULL, not CASCADE: losing the
    # claim must not delete the member's question.
    about_claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )
    # `joined`, so the conversation lists — which select MemberEnquiry entities
    # in bulk — resolve the reference in the same round trip. Lazy here would be
    # one extra SELECT per row on both surfaces, for a line of context.
    about_claim: Mapped[Claim | None] = relationship(lazy="joined")
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # NOTE: no `last_message_at`. A thread's activity time is its last message,
    # read from `claim_messages` — a maintained column here would be a write
    # obligation on every post and a second place for that fact to be wrong.
    # See `services/claim_messages._claim_thread_parts`.
