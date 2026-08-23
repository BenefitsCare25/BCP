"""Questions: threads a member starts that hang off no claim.

The thread MECHANICS are `services/claim_messages.py` — this module owns only
what is particular to a question: its vocabulary, who may open one, how many,
and the loader that keeps one member out of another's.

**A question about ONE claim is not created here.** The portal's picker routes
that member to the claim's own thread instead, because a second thread tagged to
a claim is two conversations about one thing, each readable while the other still
shows unread. `TOPICS` carries the routing option so the vocabulary — and which
option routes — has a single home; `assert_topic_storable` refuses it as a topic.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Claim, Employee, MemberEnquiry
from app.models.member_enquiry import (
    ENQUIRY_TOPICS,
    MAX_OPEN_ENQUIRIES,
    STATUS_ANSWERED,
    STATUS_CLOSED,
    STATUS_OPEN,
    topic_is_urgent,
    topic_label,
)
from app.schemas.claims import EnquiryOut, EnquiryTopicOut
from app.services.claim_messages import (
    claim_subject,
    post_member_enquiry_message,
)
from app.services.claims import load_member_claim

# The "What's it about?" picker, in the order it is shown. Rows, not a
# dropdown — six options, each a full tap target.
#
# DERIVED from `models.member_enquiry.ENQUIRY_TOPICS`, which is also what labels
# a conversation row. Two literal copies is how a topic comes to read one way in
# the picker and another in the list.
TOPICS: list[EnquiryTopicOut] = [
    EnquiryTopicOut(key=key, label=label, routes_to_claim=routes)
    for key, label, routes, _urgent in ENQUIRY_TOPICS
]

_BY_KEY = {t.key: t for t in TOPICS}


def assert_topic_storable(topic: str) -> None:
    """422 on an unknown topic, and on the ROUTING one.

    `claim` is an instruction to the form, not a subject: honouring it here
    would mint exactly the duplicate thread the routing exists to prevent.
    """
    entry = _BY_KEY.get(topic)
    if entry is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown topic '{topic}'."
        )
    if entry.routes_to_claim:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A question about one claim belongs on that claim — open it and "
            "write there.",
        )


def open_enquiry_count(db: Session, employee_id: str, policy_year_id: str) -> int:
    """How many of this member's questions are waiting on US.

    **`status == STATUS_OPEN`, not `!= STATUS_CLOSED`.** `answered` used to
    count, and nothing a MEMBER can do clears it: the first broker reply flips
    every thread to `answered`, and only a broker may close one. So five
    answered questions permanently refused the sixth — and the refusal told them
    to *"reply on one of those"*, which is an action that cannot reduce the
    count. The cap exists to stop a free-text sink feeding a queue nobody is
    paged for; a question we have already answered is not part of that.
    """
    return (
        db.scalar(
            select(func.count(MemberEnquiry.id)).where(
                MemberEnquiry.employee_id == employee_id,
                MemberEnquiry.policy_year_id == policy_year_id,
                MemberEnquiry.status == STATUS_OPEN,
            )
        )
        or 0
    )


def load_member_enquiry(
    db: Session, enquiry_id: str, employee_id: str
) -> MemberEnquiry:
    """One question, or 404 — never 403, so the portal cannot be used to
    discover whose questions exist. The point-load counterpart of the scope
    filter in `member_conversations`."""
    enquiry = db.get(MemberEnquiry, enquiry_id)
    if enquiry is None or enquiry.employee_id != employee_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return enquiry


def create_enquiry(
    db: Session,
    employee: Employee,
    *,
    topic: str,
    subject: str,
    body: str,
    about_claim_id: str | None,
    member_account_id: str,
    display_name: str | None,
) -> MemberEnquiry:
    """Open a question AND post its first message, in one transaction. Caller
    commits.

    A question with no message is a thread nobody wrote in — it would list on
    both surfaces with nothing to read and no way to tell what was meant, so
    the two are never separable.
    """
    assert_topic_storable(topic)
    if open_enquiry_count(db, employee.id, employee.policy_year_id) >= MAX_OPEN_ENQUIRIES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"You have {MAX_OPEN_ENQUIRIES} questions waiting on us. We'll "
            "answer those first — add anything else to one of them and we'll "
            "pick it up there.",
        )
    if about_claim_id:
        # Through the member's OWN loader: a claim they don't own 404s exactly
        # as it does everywhere else, so this reference can't be used to probe.
        load_member_claim(db, about_claim_id, employee.id)
    enquiry = MemberEnquiry(
        client_id=employee.client_id,
        employee_id=employee.id,
        policy_year_id=employee.policy_year_id,
        topic=topic,
        subject=subject,
        status=STATUS_OPEN,
        about_claim_id=about_claim_id or None,
    )
    db.add(enquiry)
    db.flush()
    post_member_enquiry_message(
        db,
        enquiry,
        member_account_id=member_account_id,
        display_name=display_name,
        body=body,
    )
    return enquiry


def mark_answered(enquiry: MemberEnquiry) -> None:
    """A broker replying answers the question — unless it is closed, which is a
    deliberate end state and not something a stray reply should undo."""
    if enquiry.status == STATUS_OPEN:
        enquiry.status = STATUS_ANSWERED


def close_enquiry(db: Session, enquiry: MemberEnquiry, *, user_id: str) -> None:
    """Closing is refused until a broker has replied. A thread that ends with
    nobody having answered reads as being ignored on purpose — and from the
    member's side that is indistinguishable from what it would be."""
    from app.models import ClaimMessage
    from app.models.claim_message import AUTHOR_BROKER

    answered = db.scalar(
        select(func.count(ClaimMessage.id)).where(
            ClaimMessage.enquiry_id == enquiry.id,
            ClaimMessage.author_type == AUTHOR_BROKER,
        )
    )
    if not answered:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Answer the question before closing it.",
        )
    enquiry.status = STATUS_CLOSED
    enquiry.closed_at = datetime.now(UTC)
    enquiry.closed_by_user_id = user_id


def reopen_enquiry(enquiry: MemberEnquiry) -> None:
    """Undo a close. Only a CLOSED question can be reopened.

    Without the guard the endpoint accepted `reopen` on a thread that was still
    `open` and stamped it `answered` — which the member's strike renders as the
    green "Answered" state over a thread nobody has written in. The UI only
    offers the action on a closed thread, but the endpoint is the contract.
    """
    if enquiry.status != STATUS_CLOSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "That question isn't closed, so there is nothing to reopen.",
        )
    enquiry.status = STATUS_ANSWERED
    enquiry.closed_at = None
    enquiry.closed_by_user_id = None


def enquiry_out(db: Session, enquiry: MemberEnquiry) -> EnquiryOut:
    """The header both the member's thread page and the broker's sheet read.
    The employee is added by the broker router — the member's own payload has
    no business naming them to themselves."""
    referenced = (
        db.get(Claim, enquiry.about_claim_id) if enquiry.about_claim_id else None
    )
    return EnquiryOut(
        id=enquiry.id,
        topic=enquiry.topic,
        topic_label=topic_label(enquiry.topic),
        topic_urgent=topic_is_urgent(enquiry.topic),
        subject=enquiry.subject,
        status=enquiry.status,
        about_claim=claim_subject(referenced) if referenced is not None else None,
        created_at=enquiry.created_at,
    )
