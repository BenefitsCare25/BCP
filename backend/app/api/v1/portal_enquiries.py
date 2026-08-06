"""Member question endpoints — a thread that hangs off no claim.

Every access resolves through `resolve_member_employee` and then through the
member's OWN question (`load_member_enquiry`), exactly like `portal_claims.py`
and `portal_messages.py`: an id belonging to a co-worker 404s rather than 403s,
so the portal can't be used to discover whose questions exist.

Registered in `main.py` OUTSIDE the broker router loop — `require_write_access`
assumes a broker `CurrentUser` and would reject every member token here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.portal_auth import (
    CurrentMember,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models.member_enquiry import STATUS_CLOSED
from app.schemas.claims import (
    ClaimMessageOut,
    EnquiryCreateIn,
    EnquiryOut,
    EnquiryTopicOut,
    MemberMessageIn,
    MessagesReadOut,
)
from app.services.claim_messages import (
    mark_member_read,
    member_message_out,
    post_member_enquiry_message,
    thread_for_enquiry,
)
from app.services.member_enquiries import (
    TOPICS,
    create_enquiry,
    enquiry_out,
    load_member_enquiry,
)

router = APIRouter(
    prefix="/portal",
    tags=["portal-enquiries"],
    dependencies=[Depends(get_current_member)],
)


@router.get("/enquiry-topics", response_model=list[EnquiryTopicOut])
def list_enquiry_topics() -> list[EnquiryTopicOut]:
    """The "What's it about?" picker.

    Served rather than written into the form, so the vocabulary — and which
    option ROUTES to a claim instead of creating a question — has one home.
    """
    return TOPICS


@router.post("/enquiries", response_model=EnquiryOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_my_enquiry(
    request: Request,
    body: EnquiryCreateIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> EnquiryOut:
    """Open a question. The thread and its first message are one transaction —
    a question nobody has written in would list on both surfaces with nothing
    to read."""
    employee = resolve_member_employee(db, member)
    enquiry = create_enquiry(
        db,
        employee,
        topic=body.topic,
        subject=body.subject,
        body=body.body,
        about_claim_id=body.about_claim_id,
        member_account_id=member.member_account_id,
        display_name=member.display_name,
    )
    write_member_audit(
        db, member, "enquiry.created", "enquiry", enquiry.id,
        after={"topic": enquiry.topic, "about_claim_id": enquiry.about_claim_id},
        employee_id=employee.id,
    )
    db.commit()
    return enquiry_out(db, enquiry)


@router.get("/enquiries/{enquiry_id}", response_model=EnquiryOut)
def get_my_enquiry(
    enquiry_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> EnquiryOut:
    employee = resolve_member_employee(db, member)
    return enquiry_out(db, load_member_enquiry(db, enquiry_id, employee.id))


@router.get(
    "/enquiries/{enquiry_id}/messages", response_model=list[ClaimMessageOut]
)
def list_my_enquiry_messages(
    enquiry_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[ClaimMessageOut]:
    employee = resolve_member_employee(db, member)
    enquiry = load_member_enquiry(db, enquiry_id, employee.id)
    return [member_message_out(m) for m in thread_for_enquiry(db, enquiry.id)]


@router.post(
    "/enquiries/{enquiry_id}/messages",
    response_model=ClaimMessageOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def post_my_enquiry_message(
    request: Request,
    enquiry_id: str,
    body: MemberMessageIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimMessageOut:
    """Write again on the member's own question.

    Refused once CLOSED: the thread has been ended deliberately, and a reply
    that lands in it would sit unread with nobody expecting it. The member is
    told to open a new question, which is one tap away.
    """
    employee = resolve_member_employee(db, member)
    enquiry = load_member_enquiry(db, enquiry_id, employee.id)
    if enquiry.status == STATUS_CLOSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This question is closed. Ask a new one and we'll pick it up there.",
        )
    msg = post_member_enquiry_message(
        db,
        enquiry,
        member_account_id=member.member_account_id,
        display_name=member.display_name,
        body=body.body,
    )
    write_member_audit(
        db, member, "enquiry.message_sent", "enquiry", enquiry.id,
        after={"message_id": msg.id},
        employee_id=employee.id,
    )
    db.commit()
    return member_message_out(msg)


@router.post(
    "/enquiries/{enquiry_id}/messages/read", response_model=MessagesReadOut
)
def mark_my_enquiry_messages_read(
    enquiry_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessagesReadOut:
    """Called when the member opens the thread. Not audited — reading is not an
    action on the record, and one row per page view buries the trail that is."""
    employee = resolve_member_employee(db, member)
    enquiry = load_member_enquiry(db, enquiry_id, employee.id)
    marked = mark_member_read(db, enquiry_id=enquiry.id)
    db.commit()
    return MessagesReadOut(marked=marked)
