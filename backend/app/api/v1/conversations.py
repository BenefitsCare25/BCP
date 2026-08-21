"""The broker's message queue: who is waiting on a reply.

Its own router rather than a path under `/claims`, for two reasons. A
`/claims/conversations` route would sit next to `/claims/{claim_id}` and depend
on declaration order not to be swallowed by it — fragile in a file this size.
And a conversation is not a claim: when questions land, threads that hang off
no claim at all appear in exactly this list, because from a broker's side "a
member is waiting on a reply" is ONE job regardless of what the thread is
attached to.

Runs in the normal gated broker loop; the policy year is tenant-checked with
`assert_policy_year_for_user`, the same gate `GET /claims` uses.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import assert_policy_year_for_user
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.schemas.claims import ConversationList
from app.services.claim_messages import (
    broker_conversation_out,
    broker_conversations,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationList)
def list_conversations(
    policy_year_id: str,
    awaiting: str = Query(default="us", pattern="^(us|any)$"),
    employee_id: str | None = Query(default=None),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConversationList:
    """Threads in a benefit year, as work.

    `awaiting=us` (the default) is the QUEUE: threads whose last word is the
    member's. It is what the Claims page badges, and it is deliberately the
    default — the claims queue could only ever be scrolled for badges, so the
    view that answers "who is waiting on me?" should be the one you land on.

    `awaiting=any` is for looking a thread up, not for working through, and
    sorts newest-first accordingly; the queue sorts OLDEST-first, because the
    thread that has waited longest is the one about to become a complaint.

    **The tab's badge reads `total`, not `unread_total`** — the count a broker
    needs is "how many members are waiting on me", which is threads, and a
    thread waits whether or not somebody has already opened the message. The
    two are different questions and the badge asks the first.

    `unread_total` keeps the meaning `ConversationList` gives it on every
    surface: unread MESSAGES across the whole VIEW, never just this page. It was
    briefly page-local here, which put two meanings on one shared field — the
    kind of thing a future badge reads without checking and undercounts on.
    """
    assert_policy_year_for_user(policy_year_id, user, db)
    total, unread_total, rows = broker_conversations(
        db,
        policy_year_id,
        awaiting_member=awaiting == "us",
        employee_id=employee_id,
        search=q,
        offset=offset,
        limit=limit,
    )
    return ConversationList(
        total=total,
        offset=offset,
        limit=limit,
        unread_total=unread_total,
        items=[broker_conversation_out(row, employee) for row, employee in rows],
    )
