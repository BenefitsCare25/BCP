"""Member message endpoints — the claim conversation, from the member's side.

Every access is scoped through `resolve_member_employee` and then through the
member's OWN claim (`_own_claim`), exactly like `portal_claims.py`: a claim id
belonging to a co-worker 404s rather than 403s, so the portal can't be used to
discover whose claims exist.

Registered in `main.py` OUTSIDE the broker router loop — `require_write_access`
assumes a broker `CurrentUser` and would reject every member token here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import (
    CurrentMember,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.db.session import get_db
from app.models import Claim
from app.models.claim import CLAIM_STATUS_DRAFT
from app.schemas.claims import (
    ClaimMessageList,
    ClaimMessageOut,
    MemberMessageIn,
    MessagesReadOut,
)
from app.services.claim_messages import (
    mark_member_read,
    member_inbox,
    member_message_out,
    member_unread_count,
    post_member_message,
    thread_for_claim,
)
from app.services.claims import load_member_claim

router = APIRouter(
    prefix="/portal",
    tags=["portal-messages"],
    dependencies=[Depends(get_current_member)],
)


def _own_claim(db: Session, claim_id: str, employee_id: str) -> Claim:
    """Delegates to the ONE member claim loader.

    This used to be an independent copy of `portal_claims._own_claim`, which is
    how the broker-created exclusion reached the claim surface and not this one:
    a member could read (and post to) the thread of a case that 404s everywhere
    else. A thread hangs off a claim, so it inherits that claim's visibility.
    """
    return load_member_claim(db, claim_id, employee_id)


@router.get("/messages", response_model=ClaimMessageList)
def list_my_messages(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimMessageList:
    """The member's whole inbox for the current benefit year, newest first.

    `unread` is the count across the WHOLE inbox, not just this page — it is
    what the home tile and the shell badge state, and a page-local count would
    shrink as the member paged forward.
    """
    employee = resolve_member_employee(db, member)
    total, rows = member_inbox(
        db, employee.id, employee.policy_year_id, offset=offset, limit=limit
    )
    return ClaimMessageList(
        total=total,
        offset=offset,
        limit=limit,
        unread=member_unread_count(db, employee.id, employee.policy_year_id),
        items=[member_message_out(m, c) for m, c in rows],
    )


@router.get("/claims/{claim_id}/messages", response_model=list[ClaimMessageOut])
def list_my_claim_messages(
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[ClaimMessageOut]:
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    return [member_message_out(m) for m in thread_for_claim(db, claim.id)]


@router.post(
    "/claims/{claim_id}/messages",
    response_model=ClaimMessageOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
def post_my_claim_message(
    request: Request,
    claim_id: str,
    body: MemberMessageIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimMessageOut:
    """Reply on the member's own claim.

    Refused on a DRAFT claim: nothing has been sent, so there is nobody at the
    other end — a reply there would sit unread until the member submitted, and
    reads to them as a message delivered.
    """
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    if claim.status == CLAIM_STATUS_DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Send this claim first — there's nothing to discuss until we have it.",
        )
    msg = post_member_message(
        db,
        claim,
        member_account_id=member.member_account_id,
        display_name=member.display_name,
        body=body.body,
    )
    write_member_audit(
        db, member, "claim.message_sent", "claim", claim.id,
        after={"message_id": msg.id},
        employee_id=employee.id,
    )
    db.commit()
    return member_message_out(msg)


@router.post("/claims/{claim_id}/messages/read", response_model=MessagesReadOut)
def mark_my_claim_messages_read(
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> MessagesReadOut:
    """Called when the member opens a claim's thread. Not audited — reading is
    not an action on the record, and one audit row per page view would bury the
    trail that matters."""
    employee = resolve_member_employee(db, member)
    claim = _own_claim(db, claim_id, employee.id)
    marked = mark_member_read(db, claim.id)
    db.commit()
    return MessagesReadOut(marked=marked)
