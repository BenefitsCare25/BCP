"""Broker side of a member's question.

The question does NOT get its own queue — it appears in `GET /conversations`
beside every claim thread, because from a broker's side "a member is waiting on
a reply" is one job and it does not matter what the thread hangs off. These
endpoints are what the sheet behind a row reads and writes.

Runs in the normal gated broker loop. Tenant scoping is the same rule the whole
module uses: `load_client_enquiry` joins to the caller's active client and 404s
on anything else — never 403, so a broker cannot map another tenant's records.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import MemberEnquiry
from app.models.member_enquiry import STATUS_CLOSED
from app.schemas.claims import (
    BrokerMessageIn,
    ClaimMessageOut,
    ConversationEmployeeOut,
    EnquiryOut,
    EnquiryStatusIn,
    MessagesReadOut,
)
from app.services.claim_messages import (
    broker_message_out,
    mark_broker_read,
    post_broker_enquiry_message,
    thread_for_enquiry,
)
from app.services.member_enquiries import (
    close_enquiry,
    enquiry_out,
    mark_answered,
    reopen_enquiry,
)

router = APIRouter(prefix="/enquiries", tags=["enquiries"])


def load_client_enquiry(
    db: Session, enquiry_id: str, user: CurrentUser
) -> MemberEnquiry:
    """The question, tenant-checked. 404 on another client's, per the standing
    rule — don't leak that a resource exists."""
    enquiry = db.get(MemberEnquiry, enquiry_id)
    client_id = require_client_id(user)
    if enquiry is None or (
        enquiry.client_id != client_id and user.role != "system_admin"
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return enquiry


def _with_employee(db: Session, enquiry: MemberEnquiry) -> EnquiryOut:
    from app.models import Employee

    out = enquiry_out(db, enquiry)
    employee = db.get(Employee, enquiry.employee_id)
    if employee is not None:
        out.employee = ConversationEmployeeOut(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        )
    return out


@router.get("/{enquiry_id}", response_model=EnquiryOut)
def get_enquiry(
    enquiry_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnquiryOut:
    return _with_employee(db, load_client_enquiry(db, enquiry_id, user))


@router.get("/{enquiry_id}/messages", response_model=list[ClaimMessageOut])
def list_enquiry_messages(
    enquiry_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ClaimMessageOut]:
    enquiry = load_client_enquiry(db, enquiry_id, user)
    return [broker_message_out(m) for m in thread_for_enquiry(db, enquiry.id)]


@router.post(
    "/{enquiry_id}/messages",
    response_model=ClaimMessageOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def post_enquiry_message(
    request: Request,
    enquiry_id: str,
    body: BrokerMessageIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ClaimMessageOut:
    """Answer the member.

    **Everything sent from here is read by the member** — there is no
    internal-note mode, for the reason `models/claim_message.py` gives.
    """
    enquiry = load_client_enquiry(db, enquiry_id, user)
    if enquiry.status == STATUS_CLOSED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This question is closed. Reopen it before writing.",
        )
    msg = post_broker_enquiry_message(
        db, enquiry, user_id=user.user_id, body=body.body, subject=body.subject
    )
    mark_answered(enquiry)
    write_audit(
        db, user, "enquiry.message_sent", "enquiry", enquiry.id,
        after={"message_id": msg.id},
        employee_id=enquiry.employee_id,
    )
    db.commit()
    return broker_message_out(msg)


@router.post("/{enquiry_id}/messages/read", response_model=MessagesReadOut)
def mark_enquiry_messages_read(
    enquiry_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessagesReadOut:
    enquiry = load_client_enquiry(db, enquiry_id, user)
    marked = mark_broker_read(db, enquiry_id=enquiry.id)
    db.commit()
    return MessagesReadOut(marked=marked)


@router.post("/{enquiry_id}/status", response_model=EnquiryOut)
def set_enquiry_status(
    enquiry_id: str,
    body: EnquiryStatusIn,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnquiryOut:
    """Close or reopen. Closing 409s until a broker has replied — see
    `close_enquiry`."""
    enquiry = load_client_enquiry(db, enquiry_id, user)
    before = {"status": enquiry.status}
    if body.action == "close":
        close_enquiry(db, enquiry, user_id=user.user_id)
    else:
        reopen_enquiry(enquiry)
    write_audit(
        db, user, f"enquiry.{body.action}", "enquiry", enquiry.id,
        before=before, after={"status": enquiry.status},
        employee_id=enquiry.employee_id,
    )
    db.commit()
    return _with_employee(db, enquiry)
