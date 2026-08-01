"""The claim conversation: who may write, what the automatic notices say, and
what each surface is allowed to read back.

Three rules here are load-bearing, and each of them fails quietly if broken.

**1. The member never learns which broker replied.** `author_name` stores the
real identity (so the broker thread is useful and the trail is complete), and
`member_message_out` substitutes the team label on the way out. That
substitution lives HERE, beside the broker builder, rather than in the portal
router — a router that forgets it leaks a staff name into a member's inbox, and
nothing about the response shape would show it.

**2. A system notice is written from the claim, once, at the moment it moves.**
It is a RECORD of what the member was told, not a template rendered on read: the
claim's amount, status and decision note all keep changing afterwards, and a
message that re-renders would rewrite history every time the inbox loads.

**3. Posting never commits.** Every caller is already inside a claim
transaction (submit, decide) — committing here would persist a notice for a
decision that then rolls back.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Claim, ClaimMessage, User
from app.models.claim_message import (
    AUTHOR_BROKER,
    AUTHOR_MEMBER,
    AUTHOR_SYSTEM,
    EVENT_APPROVED,
    EVENT_NEEDS_INFO,
    EVENT_REJECTED,
    EVENT_SUBMITTED,
)
from app.schemas.claims import ClaimMessageOut

# What a member sees as the author of anything written by us. Deliberately a
# team, not a person: a claim is handled by whoever picks it up, and naming an
# individual invites the member to chase them directly.
TEAM_NAME = "Claims team"

# Subject line for a broker's free-text message when they don't set one. It has
# to stand alone in the inbox list, where the body is only a snippet.
DEFAULT_BROKER_SUBJECT = "A message about your claim"

# A member's reply needs a subject too (the inbox lists subjects), but the
# member never types one — it would be a second box for no information.
MEMBER_REPLY_SUBJECT = "Your reply"


# Currency as a member writes it, not as the database stores it. This is the
# ONE place the backend renders money into member-facing prose, and it has to
# agree with the portal's `leaf/Figure.tsx::currencySymbol` — a notice reading
# "SGD 88.40" beside a page printing "S$88.40" reads as two different systems
# talking about one claim. Unknown codes pass through rather than being guessed.
_SYMBOL = {"SGD": "S$", "MYR": "RM", "USD": "US$", "EUR": "€", "GBP": "£"}


def _money(claim: Claim, amount: float | None) -> str:
    if amount is None:
        return ""
    code = (claim.currency or "").strip().upper()
    symbol = _SYMBOL.get(code)
    # Cents in full when there are any, omitted when there are none — the same
    # rule `moneyText` follows, so a round figure isn't given a decorative
    # ".00" the member's receipt doesn't have.
    figure = f"{amount:,.2f}" if round(abs(amount) * 100) % 100 else f"{amount:,.0f}"
    return f"{symbol}{figure}" if symbol else f"{code} {figure}".strip()


def _what(claim: Claim) -> str:
    """How the claim is named to its own claimant — the words they picked on the
    form, never a product code."""
    return claim.claim_type or "claim"


def _system_copy(claim: Claim, event: str, note: str | None) -> tuple[str, str]:
    """(subject, body) for an automatic notice.

    No turnaround is promised anywhere in here. The AI pre-check runs at once
    but the decision is a person's, and prod cannot yet email a member when it
    lands — so a date we'd have to keep would be a date we'd break.
    """
    when = claim.incurred_date.strftime("%d %b %Y")
    if event == EVENT_SUBMITTED:
        return (
            "We have your claim",
            f"Your {_what(claim)} claim for {_money(claim, claim.amount_claimed)} "
            f"on {when} is with us. You don't need to send it again — if we need "
            f"anything else, it will appear here.",
        )
    if event == EVENT_APPROVED:
        approved = _money(claim, claim.amount_approved)
        body = (
            f"Your {_what(claim)} claim for {when} has been approved"
            + (f" for {approved}." if approved else ".")
        )
        return "Your claim is approved", _join(body, note)
    if event == EVENT_REJECTED:
        return (
            "Your claim was not approved",
            _join(
                f"We weren't able to approve your {_what(claim)} claim for {when}.",
                note,
                "If you think something has been missed, reply here and we'll "
                "take another look.",
            ),
        )
    if event == EVENT_NEEDS_INFO:
        return (
            "We need something else",
            _join(
                f"Before we can finish your {_what(claim)} claim for {when}, we "
                f"need a little more from you.",
                note,
                "Open the claim to add what's missing, then send it again.",
            ),
        )
    # Unknown event: post a neutral notice rather than dropping the message. A
    # member seeing a plain line is recoverable; a silent gap in the thread is
    # the kind of thing nobody discovers until someone asks why they were never
    # told.
    return "An update on your claim", _join(f"Your {_what(claim)} claim was updated.", note)


def _join(*parts: str | None) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _post(db: Session, msg: ClaimMessage) -> ClaimMessage:
    """Add a message with an EXPLICIT `created_at`, then flush.

    The column's `server_default=now()` cannot order a thread. On SQLite it
    resolves to whole seconds, and on Postgres `now()` is TRANSACTION start
    time — so two messages written in one request (a member replies as a
    decision lands, a resubmission acknowledged beside its notice) get
    identical timestamps and the thread then sorts on a random uuid. A
    conversation whose order is arbitrary is worse than no conversation:
    "we need the itemised bill" can print below "thanks, here it is".
    """
    msg.created_at = datetime.now(UTC)
    db.add(msg)
    db.flush()
    return msg


def post_system_message(
    db: Session, claim: Claim, event: str, *, note: str | None = None
) -> ClaimMessage:
    """Post the automatic notice for a claim event. Caller commits."""
    subject, body = _system_copy(claim, event, note)
    return _post(
        db,
        ClaimMessage(
            client_id=claim.client_id,
            claim_id=claim.id,
            author_type=AUTHOR_SYSTEM,
            author_name=TEAM_NAME,
            subject=subject,
            body=body,
            event=event,
        ),
    )


def post_broker_message(
    db: Session,
    claim: Claim,
    *,
    user_id: str,
    body: str,
    subject: str | None = None,
) -> ClaimMessage:
    """A broker writing to the member. Caller commits."""
    author = db.get(User, user_id)
    return _post(
        db,
        ClaimMessage(
            client_id=claim.client_id,
            claim_id=claim.id,
            author_type=AUTHOR_BROKER,
            author_user_id=user_id,
            # Real identity for the broker thread + the trail; the member-facing
            # builder replaces it with the team label.
            author_name=(
                (author.display_name or author.email) if author is not None else None
            ),
            subject=(subject or "").strip() or DEFAULT_BROKER_SUBJECT,
            body=body.strip(),
        ),
    )


def post_member_message(
    db: Session,
    claim: Claim,
    *,
    member_account_id: str,
    display_name: str | None,
    body: str,
) -> ClaimMessage:
    """A member replying on their own claim. Caller commits."""
    return _post(
        db,
        ClaimMessage(
            client_id=claim.client_id,
            claim_id=claim.id,
            author_type=AUTHOR_MEMBER,
            author_member_id=member_account_id,
            author_name=display_name,
            subject=MEMBER_REPLY_SUBJECT,
            body=body.strip(),
        ),
    )


# ── Reads ─────────────────────────────────────────────────────────────────────


def thread_for_claim(db: Session, claim_id: str) -> list[ClaimMessage]:
    """The whole conversation, oldest first — a thread is read downwards."""
    return list(
        db.execute(
            select(ClaimMessage)
            .where(ClaimMessage.claim_id == claim_id)
            .order_by(ClaimMessage.created_at, ClaimMessage.id)
        ).scalars().all()
    )


def member_inbox(
    db: Session, employee_id: str, policy_year_id: str, *, offset: int, limit: int
) -> tuple[int, list[tuple[ClaimMessage, Claim]]]:
    """(total, [(message, claim)]) newest first — the member's whole inbox
    across every claim of the year.

    Scoped through a JOIN on `claims` rather than a denormalized `employee_id`
    column: the claim already owns that fact, and a copy of it is a second place
    for a member's inbox to go wrong.
    """
    owned = (
        Claim.employee_id == employee_id,
        Claim.policy_year_id == policy_year_id,
    )
    total = (
        db.scalar(
            select(func.count(ClaimMessage.id))
            .select_from(ClaimMessage)
            .join(Claim, ClaimMessage.claim_id == Claim.id)
            .where(*owned)
        )
        or 0
    )
    rows = db.execute(
        select(ClaimMessage, Claim)
        .join(Claim, ClaimMessage.claim_id == Claim.id)
        .where(*owned)
        .order_by(ClaimMessage.created_at.desc(), ClaimMessage.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return total, [(m, c) for m, c in rows]


def member_unread_count(db: Session, employee_id: str, policy_year_id: str) -> int:
    """How many messages the member hasn't opened. Their OWN replies never
    count — a surface that badges you for something you just wrote is noise."""
    return (
        db.scalar(
            select(func.count(ClaimMessage.id))
            .select_from(ClaimMessage)
            .join(Claim, ClaimMessage.claim_id == Claim.id)
            .where(
                Claim.employee_id == employee_id,
                Claim.policy_year_id == policy_year_id,
                ClaimMessage.author_type != AUTHOR_MEMBER,
                ClaimMessage.member_read_at.is_(None),
            )
        )
        or 0
    )


def mark_member_read(db: Session, claim_id: str) -> int:
    """Mark everything the member can see on this claim as read. Caller commits.
    Returns how many rows changed."""
    changed = 0
    for msg in db.execute(
        select(ClaimMessage).where(
            ClaimMessage.claim_id == claim_id,
            ClaimMessage.author_type != AUTHOR_MEMBER,
            ClaimMessage.member_read_at.is_(None),
        )
    ).scalars():
        msg.member_read_at = datetime.now(UTC)
        changed += 1
    return changed


def mark_broker_read(db: Session, claim_id: str) -> int:
    """Mark the member's replies on this claim as seen. Caller commits."""
    changed = 0
    for msg in db.execute(
        select(ClaimMessage).where(
            ClaimMessage.claim_id == claim_id,
            ClaimMessage.author_type == AUTHOR_MEMBER,
            ClaimMessage.broker_read_at.is_(None),
        )
    ).scalars():
        msg.broker_read_at = datetime.now(UTC)
        changed += 1
    return changed


# ── Serializers, one per surface ──────────────────────────────────────────────


def member_message_out(msg: ClaimMessage, claim: Claim | None = None) -> ClaimMessageOut:
    """What the MEMBER may see. `mine` marks their own replies (the thread
    aligns them differently); `unread` is about them, not about the broker."""
    return ClaimMessageOut(
        id=msg.id,
        claim_id=msg.claim_id,
        author_type=msg.author_type,
        # Rule 1: never the individual broker.
        author_name=(
            msg.author_name if msg.author_type == AUTHOR_MEMBER else TEAM_NAME
        ),
        subject=msg.subject,
        body=msg.body,
        event=msg.event,
        created_at=msg.created_at,
        mine=msg.author_type == AUTHOR_MEMBER,
        unread=msg.author_type != AUTHOR_MEMBER and msg.member_read_at is None,
        claim_type=claim.claim_type if claim is not None else None,
        claim_status=claim.status if claim is not None else None,
    )


def broker_message_out(msg: ClaimMessage, claim: Claim | None = None) -> ClaimMessageOut:
    """What a BROKER sees: the real author, and unread meaning "the member
    wrote this and nobody here has opened it"."""
    return ClaimMessageOut(
        id=msg.id,
        claim_id=msg.claim_id,
        author_type=msg.author_type,
        author_name=msg.author_name or (TEAM_NAME if msg.author_type == AUTHOR_SYSTEM else None),
        subject=msg.subject,
        body=msg.body,
        event=msg.event,
        created_at=msg.created_at,
        mine=msg.author_type != AUTHOR_MEMBER,
        unread=msg.author_type == AUTHOR_MEMBER and msg.broker_read_at is None,
        claim_type=claim.claim_type if claim is not None else None,
        claim_status=claim.status if claim is not None else None,
    )
