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

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, aliased

from app.models import Claim, ClaimMessage, Employee, MemberEnquiry, User
from app.models.claim import member_visible_claims
from app.models.claim_message import (
    AUTHOR_BROKER,
    AUTHOR_MEMBER,
    AUTHOR_SYSTEM,
    EVENT_APPROVED,
    EVENT_NEEDS_INFO,
    EVENT_PAID,
    EVENT_REJECTED,
    EVENT_SUBMITTED,
)
from app.models.member_enquiry import (
    URGENT_TOPICS,
    topic_is_urgent,
    topic_label,
)
from app.schemas.claims import (
    ClaimMessageOut,
    ConversationEmployeeOut,
    ConversationOut,
    ConversationSubjectOut,
)

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
    if event == EVENT_PAID:
        # Quote what the INSURER paid, not what we approved. When the two
        # differ, the member is about to see the smaller number in their bank
        # and the notice has to be the one that matches it.
        # `is not None`, NOT `or`: a ZERO settlement is a real advice (fully
        # offset against an excess), and `ClaimPaymentIn` accepts it for exactly
        # that reason. `or` falls through to what we approved and tells the
        # member they were paid a sum that never left the insurer.
        paid = _money(
            claim,
            claim.payment_amount
            if claim.payment_amount is not None
            else claim.amount_approved,
        )
        on = claim.paid_on.strftime("%d %b %Y") if claim.paid_on else None
        return (
            "Your claim has been paid",
            _join(
                f"Your {_what(claim)} claim for {when} has been paid"
                + (f" — {paid}" if paid else "")
                + (f" on {on}." if on else "."),
                note,
                "It can take a few working days to reach your account.",
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

    It is also the ONE place the owner invariant is enforced — exactly one of
    `claim_id` / `enquiry_id`. Not a DB CHECK: `sync_firm_schema` propagates new
    tables and columns to firm schemas, never constraints, so a CHECK would hold
    in `public` and nowhere else — which reads as enforced and is not. A message
    with neither owner belongs to no thread and is invisible on every surface; a
    message with both would appear in two.
    """
    if bool(msg.claim_id) == bool(msg.enquiry_id):
        raise ValueError(
            "a message must belong to exactly one thread "
            "(claim_id or enquiry_id, never both and never neither)"
        )
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


def post_member_enquiry_message(
    db: Session,
    enquiry: MemberEnquiry,
    *,
    member_account_id: str,
    display_name: str | None,
    body: str,
) -> ClaimMessage:
    """A member writing on their own question. Caller commits.

    A thin twin of `post_member_message` rather than one function taking either
    owner: the rules that must not drift live in `_post` and in the two
    serializers, and both builders go through them.
    """
    return _post(
        db,
        ClaimMessage(
            client_id=enquiry.client_id,
            enquiry_id=enquiry.id,
            author_type=AUTHOR_MEMBER,
            author_member_id=member_account_id,
            author_name=display_name,
            subject=MEMBER_REPLY_SUBJECT,
            body=body.strip(),
        ),
    )


def post_broker_enquiry_message(
    db: Session,
    enquiry: MemberEnquiry,
    *,
    user_id: str,
    body: str,
    subject: str | None = None,
) -> ClaimMessage:
    """A broker answering a question. Caller commits."""
    author = db.get(User, user_id)
    return _post(
        db,
        ClaimMessage(
            client_id=enquiry.client_id,
            enquiry_id=enquiry.id,
            author_type=AUTHOR_BROKER,
            author_user_id=user_id,
            author_name=(
                (author.display_name or author.email) if author is not None else None
            ),
            # A question already HAS the member's own headline. Echoing it beats
            # the generic broker subject, which would say nothing about which
            # question had been answered.
            subject=(subject or "").strip() or enquiry.subject,
            body=body.strip(),
        ),
    )


# ── Reads ─────────────────────────────────────────────────────────────────────


def _owned_by(claim_id: str | None, enquiry_id: str | None):
    """The filter selecting ONE thread's messages, under the same
    exactly-one-owner invariant `_post` writes with. A caller that named
    neither would otherwise read (or mark read) every message in the tenant."""
    if bool(claim_id) == bool(enquiry_id):
        raise ValueError("name exactly one thread (claim_id or enquiry_id)")
    return (
        ClaimMessage.claim_id == claim_id
        if claim_id
        else ClaimMessage.enquiry_id == enquiry_id
    )


def _thread(
    db: Session, *, claim_id: str | None = None, enquiry_id: str | None = None
) -> list[ClaimMessage]:
    return list(
        db.execute(
            select(ClaimMessage)
            .where(_owned_by(claim_id, enquiry_id))
            .order_by(ClaimMessage.created_at, ClaimMessage.id)
        ).scalars().all()
    )


def thread_for_claim(db: Session, claim_id: str) -> list[ClaimMessage]:
    """The whole conversation, oldest first — a thread is read downwards."""
    return _thread(db, claim_id=claim_id)


def thread_for_enquiry(db: Session, enquiry_id: str) -> list[ClaimMessage]:
    return _thread(db, enquiry_id=enquiry_id)


def member_unread_count(db: Session, employee_id: str, policy_year_id: str) -> int:
    """How many messages the member hasn't opened, across BOTH kinds of thread.

    Their OWN replies never count — a surface that badges you for something you
    just wrote is noise.

    **The question half is not optional.** This is the sole source of
    `ConversationList.unread_total`, which is what the portal shell's Messages
    badge and the home tile read; with only the claim join, a broker answering a
    question left the member's row reporting `unread: 1` while the badge that
    would have told them to look reported 0. The two counts are separate
    statements rather than one outer-joined query because a message belongs to
    exactly one thread (`_post` enforces it), so there is nothing to reconcile —
    and an `OR` across two joins would multiply rows.
    """
    claim_side = (
        db.scalar(
            select(func.count(ClaimMessage.id))
            .select_from(ClaimMessage)
            .join(Claim, ClaimMessage.claim_id == Claim.id)
            .where(
                Claim.employee_id == employee_id,
                Claim.policy_year_id == policy_year_id,
                member_visible_claims(),
                ClaimMessage.author_type != AUTHOR_MEMBER,
                ClaimMessage.member_read_at.is_(None),
            )
        )
        or 0
    )
    question_side = (
        db.scalar(
            select(func.count(ClaimMessage.id))
            .select_from(ClaimMessage)
            .join(MemberEnquiry, ClaimMessage.enquiry_id == MemberEnquiry.id)
            .where(
                MemberEnquiry.employee_id == employee_id,
                MemberEnquiry.policy_year_id == policy_year_id,
                ClaimMessage.author_type != AUTHOR_MEMBER,
                ClaimMessage.member_read_at.is_(None),
            )
        )
        or 0
    )
    return claim_side + question_side


def mark_member_read(
    db: Session, *, claim_id: str | None = None, enquiry_id: str | None = None
) -> int:
    """Mark everything the member can see on this thread as read. Caller
    commits. Returns how many rows changed."""
    changed = 0
    for msg in db.execute(
        select(ClaimMessage).where(
            _owned_by(claim_id, enquiry_id),
            ClaimMessage.author_type != AUTHOR_MEMBER,
            ClaimMessage.member_read_at.is_(None),
        )
    ).scalars():
        msg.member_read_at = datetime.now(UTC)
        changed += 1
    return changed


def mark_broker_read(
    db: Session, *, claim_id: str | None = None, enquiry_id: str | None = None
) -> int:
    """Mark the member's replies on this thread as seen. Caller commits."""
    changed = 0
    for msg in db.execute(
        select(ClaimMessage).where(
            _owned_by(claim_id, enquiry_id),
            ClaimMessage.author_type == AUTHOR_MEMBER,
            ClaimMessage.broker_read_at.is_(None),
        )
    ).scalars():
        msg.broker_read_at = datetime.now(UTC)
        changed += 1
    return changed


# ── Conversations ─────────────────────────────────────────────────────────────
#
# A thread, not a message, is the unit of work on both surfaces: the member asks
# "which of my claims is this about?", the broker asks "who is waiting on me?".
# The flat inbox this replaces could answer neither — a claim TYPE is not unique
# on a real roster, so two of one member's conversations printed the same title
# and were separated only by a date inside a clamped snippet.


@dataclass(frozen=True)
class ConversationRow:
    """One thread with the counts that make it a work item.

    `last` is also how "who is this waiting on?" is answered — DERIVED from the
    thread rather than stored as a workflow state, so there is nothing to fall
    out of step with the conversation it describes.
    """

    claim: Claim | None
    enquiry: MemberEnquiry | None
    last: ClaimMessage
    message_count: int
    unread: int


def _member_unread_case():
    """A message the MEMBER hasn't opened. Mirrors `member_unread_count` — their
    own replies never count, per the two-read-column rule in the model."""
    return case(
        (
            and_(
                ClaimMessage.author_type != AUTHOR_MEMBER,
                ClaimMessage.member_read_at.is_(None),
            ),
            1,
        ),
        else_=0,
    )


def _thread_parts(unread_case, owner_col, scope):
    """Two derived tables over `claim_messages`, keyed by whichever column OWNS
    the thread — `claim_id` for a claim's conversation, `enquiry_id` for a
    question's.

    `agg`    — COUNT(*) and the asking surface's unread sum, per thread.
    `latest` — the id of each thread's LAST message, by `ROW_NUMBER`.

    Joining `latest` into the main query rather than fetching it per page is
    what lets a caller both ORDER and FILTER on the last message — which is how
    the broker's "waiting on us" view is expressed — without a second round
    trip. Both are plain SQL:1999 and run unchanged on SQLite and Postgres.

    One implementation, called once per owner, rather than a second copy for
    questions: the ordering rule, the tie-break and the unread arithmetic are
    the parts that must not drift.

    **`scope` (a SELECT of owner ids) is not optional, and it is the
    difference between an indexed read and a table scan.** A window function
    blocks predicate pushdown, so the outer join's `employee_id` /
    `policy_year_id` filters cannot reach `ranked` — unscoped, every request
    ranks the whole of `claim_messages` for the firm, twice (once for the rows,
    once for the counts). That replaced `member_inbox`, which was an indexed
    join with a LIMIT, and the shell now asks for conversations on EVERY portal
    page rather than two of them, so the cost lands everywhere.

    Restricting by `claim_id` is safe precisely because the partition IS
    `claim_id`: dropping whole partitions cannot change a rank inside a
    surviving one, so the scoped result is identical to the unscoped one.
    """
    in_scope = owner_col.in_(scope)
    agg = (
        select(
            owner_col.label("thread_id"),
            func.count(ClaimMessage.id).label("total"),
            func.coalesce(func.sum(unread_case), 0).label("unread"),
        )
        .where(in_scope)
        .group_by(owner_col)
        .subquery()
    )
    ranked = (
        select(
            ClaimMessage.id.label("mid"),
            owner_col.label("cid"),
            func.row_number()
            .over(
                partition_by=owner_col,
                # `id` breaks a tie. `_post` stamps microseconds so a tie is
                # near-impossible — but "near" would make the last word of a
                # thread nondeterministic, and the last word is the whole point
                # of this list.
                order_by=(ClaimMessage.created_at.desc(), ClaimMessage.id.desc()),
            )
            .label("rn"),
        )
        .where(in_scope)
        .subquery()
    )
    latest = select(ranked.c.mid, ranked.c.cid).where(ranked.c.rn == 1).subquery()
    return agg, latest, aliased(ClaimMessage)


def member_conversations(
    db: Session, employee_id: str, policy_year_id: str, *, offset: int, limit: int
) -> tuple[int, list[ConversationRow]]:
    """(total, rows) — the member's threads, most recently active first.

    Scoped through a JOIN on `claims` rather than a denormalized column: the
    claim already owns that fact. `member_visible_claims()` is welded to that
    join for the same reason the old inbox needed it — without it a decision
    notice on a broker-recorded case surfaces a conversation the member never
    started and whose claim 404s when they tap it.
    """
    owned = (
        Claim.employee_id == employee_id,
        Claim.policy_year_id == policy_year_id,
        member_visible_claims(),
    )
    unread_case = _member_unread_case()

    agg, latest, last = _thread_parts(
        unread_case, ClaimMessage.claim_id, select(Claim.id).where(*owned)
    )
    claim_stmt = (
        select(Claim, last, agg.c.total, agg.c.unread)
        .join(agg, agg.c.thread_id == Claim.id)
        .join(latest, latest.c.cid == Claim.id)
        .join(last, last.id == latest.c.mid)
        .where(*owned)
    )

    asked = (
        MemberEnquiry.employee_id == employee_id,
        MemberEnquiry.policy_year_id == policy_year_id,
    )
    q_agg, q_latest, q_last = _thread_parts(
        unread_case,
        ClaimMessage.enquiry_id,
        select(MemberEnquiry.id).where(*asked),
    )
    enquiry_stmt = (
        select(MemberEnquiry, q_last, q_agg.c.total, q_agg.c.unread)
        .join(q_agg, q_agg.c.thread_id == MemberEnquiry.id)
        .join(q_latest, q_latest.c.cid == MemberEnquiry.id)
        .join(q_last, q_last.id == q_latest.c.mid)
        .where(*asked)
    )

    total = (
        db.scalar(select(func.count()).select_from(claim_stmt.subquery())) or 0
    ) + (db.scalar(select(func.count()).select_from(enquiry_stmt.subquery())) or 0)

    # Merged in Python rather than UNIONed. The two sides key on different
    # columns, so one query would have to join on a nullable pair — which needs
    # `IS NOT DISTINCT FROM` on Postgres and `IS` on SQLite, or a synthetic
    # composite key — for no gain: taking `offset + limit` from each bounds the
    # read by the page depth, and the slice below is exact.
    depth = offset + limit
    rows = [
        ConversationRow(claim=c, enquiry=None, last=m, message_count=t, unread=u)
        for c, m, t, u in db.execute(
            claim_stmt.order_by(last.created_at.desc(), Claim.id.desc()).limit(depth)
        ).all()
    ] + [
        ConversationRow(claim=None, enquiry=q, last=m, message_count=t, unread=u)
        for q, m, t, u in db.execute(
            enquiry_stmt.order_by(
                q_last.created_at.desc(), MemberEnquiry.id.desc()
            ).limit(depth)
        ).all()
    ]
    rows.sort(key=lambda r: (r.last.created_at, r.last.id), reverse=True)
    return total, rows[offset : offset + limit]


def _broker_unread_case():
    """A member's message nobody here has opened. The mirror of the member's
    case — same two columns, opposite sense, which is exactly why the model
    keeps them apart."""
    return case(
        (
            and_(
                ClaimMessage.author_type == AUTHOR_MEMBER,
                ClaimMessage.broker_read_at.is_(None),
            ),
            1,
        ),
        else_=0,
    )


def _row_is_urgent(row: ConversationRow) -> bool:
    """Only a question can be urgent — urgency is a property of the TOPIC, and a
    claim thread has none. Kept beside the queue that uses it so the Python merge
    and the SQL `urgent_first` above can never disagree about what counts."""
    return row.enquiry is not None and topic_is_urgent(row.enquiry.topic)


def broker_conversations(
    db: Session,
    policy_year_id: str,
    *,
    awaiting_member: bool,
    employee_id: str | None,
    offset: int,
    limit: int,
) -> tuple[int, int, list[tuple[ConversationRow, Employee]]]:
    """(total, unread_total, [(row, employee)]) — threads in a benefit year.

    `awaiting_member` is the queue: threads whose LAST word is the member's, so
    the ball is with us. Derived from the thread rather than a stored workflow
    state, which is why it cannot fall out of step — and why it is expressed as
    a filter on the joined last message rather than computed per page.

    The two views sort differently, and each is obviously right for its own:

    * **waiting on us → OLDEST first.** In a queue the thing that has waited
      longest is the one about to become a complaint, so it belongs at the top.
    * **everything → NEWEST first**, because that view is for looking something
      up, not for working through.

    NOTE this deliberately does NOT apply `member_visible_claims()`. A broker is
    entitled to see every thread on the claims they hold, including the notices
    posted on a case an assessor recorded. Those can never enter the waiting-on-
    us view — a member who cannot see a claim cannot write on it — so the queue
    stays honest either way.
    """
    scope = [Claim.policy_year_id == policy_year_id]
    if employee_id:
        scope.append(Claim.employee_id == employee_id)
    unread_case = _broker_unread_case()
    agg, latest, last = _thread_parts(
        unread_case, ClaimMessage.claim_id, select(Claim.id).where(*scope)
    )
    conditions = list(scope)
    if awaiting_member:
        conditions.append(last.author_type == AUTHOR_MEMBER)
    joins = (
        select(Claim, Employee, last, agg.c.total, agg.c.unread)
        .join(Employee, Claim.employee_id == Employee.id)
        .join(agg, agg.c.thread_id == Claim.id)
        .join(latest, latest.c.cid == Claim.id)
        .join(last, last.id == latest.c.mid)
        .where(*conditions)
    )

    q_scope = [MemberEnquiry.policy_year_id == policy_year_id]
    if employee_id:
        q_scope.append(MemberEnquiry.employee_id == employee_id)
    q_agg, q_latest, q_last = _thread_parts(
        unread_case,
        ClaimMessage.enquiry_id,
        select(MemberEnquiry.id).where(*q_scope),
    )
    q_conditions = list(q_scope)
    if awaiting_member:
        q_conditions.append(q_last.author_type == AUTHOR_MEMBER)
    q_joins = (
        select(MemberEnquiry, Employee, q_last, q_agg.c.total, q_agg.c.unread)
        .join(Employee, MemberEnquiry.employee_id == Employee.id)
        .join(q_agg, q_agg.c.thread_id == MemberEnquiry.id)
        .join(q_latest, q_latest.c.cid == MemberEnquiry.id)
        .join(q_last, q_last.id == q_latest.c.mid)
        .where(*q_conditions)
    )
    # Count and unread-sum in ONE pass over the same joined set, so
    # `unread_total` means what the shared schema says it means — every unread
    # message in the VIEW, not just on this page — at no extra round trip.
    total, unread_total = db.execute(
        select(func.count(), func.coalesce(func.sum(agg.c.unread), 0))
        .select_from(Claim)
        .join(Employee, Claim.employee_id == Employee.id)
        .join(agg, agg.c.thread_id == Claim.id)
        .join(latest, latest.c.cid == Claim.id)
        .join(last, last.id == latest.c.mid)
        .where(*conditions)
    ).one()
    q_total, q_unread = db.execute(
        select(func.count(), func.coalesce(func.sum(q_agg.c.unread), 0))
        .select_from(MemberEnquiry)
        .join(q_agg, q_agg.c.thread_id == MemberEnquiry.id)
        .join(q_latest, q_latest.c.cid == MemberEnquiry.id)
        .join(q_last, q_last.id == q_latest.c.mid)
        .where(*q_conditions)
    ).one()
    total += q_total
    unread_total += q_unread
    # Oldest-first on the queue (longest wait at the top), newest-first when
    # looking a thread up. Applied to BOTH sides and to the merge, so the two
    # kinds interleave by the same rule rather than one being appended.
    depth = offset + limit
    order = (
        (last.created_at.asc(), Claim.id.asc())
        if awaiting_member
        else (last.created_at.desc(), Claim.id.desc())
    )
    # An URGENT topic outranks the wait, in the queue only.
    #
    # The queue sorts oldest-first, which is right for everything except the one
    # thing that cannot wait: a Letter of Guarantee request is minutes old by
    # definition, so oldest-first buries the newly-admitted member at the BOTTOM
    # of the list. It is applied to the SQL order as well as to the merge below,
    # because each side is cut to `depth` rows before they meet — sorting only
    # in Python would let a busy queue drop the urgent thread before the merge
    # ever saw it. "All" is a lookup view and keeps pure recency.
    urgent_first = case((MemberEnquiry.topic.in_(URGENT_TOPICS), 0), else_=1)
    q_order = (
        (urgent_first.asc(), q_last.created_at.asc(), MemberEnquiry.id.asc())
        if awaiting_member
        else (q_last.created_at.desc(), MemberEnquiry.id.desc())
    )
    merged = [
        (ConversationRow(claim=c, enquiry=None, last=m, message_count=t, unread=u), e)
        for c, e, m, t, u in db.execute(joins.order_by(*order).limit(depth)).all()
    ] + [
        (ConversationRow(claim=None, enquiry=q, last=m, message_count=t, unread=u), e)
        for q, e, m, t, u in db.execute(q_joins.order_by(*q_order).limit(depth)).all()
    ]
    if awaiting_member:
        merged.sort(
            key=lambda pair: (
                0 if _row_is_urgent(pair[0]) else 1,
                pair[0].last.created_at,
                pair[0].last.id,
            )
        )
    else:
        merged.sort(
            key=lambda pair: (pair[0].last.created_at, pair[0].last.id),
            reverse=True,
        )
    return total, unread_total, merged[offset : offset + limit]


def broker_conversation_out(
    row: ConversationRow, employee: Employee
) -> ConversationOut:
    """A conversation as a BROKER sees it: the real author on the last message,
    `unread` meaning "the member wrote this and nobody here has opened it", and
    the employee named — which the member's own list has no business carrying
    and is the whole point of the broker's."""
    return ConversationOut(
        subject=subject_of(row),
        last_message=broker_message_out(row.last),
        message_count=row.message_count,
        unread=row.unread,
        employee=ConversationEmployeeOut(
            id=employee.id,
            staff_id=employee.staff_id,
            employee_name=employee.employee_name,
        ),
    )


def claim_subject(claim: Claim) -> ConversationSubjectOut:
    """The claim, reduced to what names a conversation. Deliberately NOT the
    whole `ClaimOut`: a list row has no use for documents or slots, and loading
    them would put the claim detail's cost on every row of the inbox."""
    return ConversationSubjectOut(
        kind="claim",
        id=claim.id,
        claim_kind=claim.claim_kind,
        claim_type=claim.claim_type,
        sub_type=claim.sub_type,
        product_code=claim.product_code,
        flex_category_name=claim.flex_category_name,
        incurred_date=claim.incurred_date,
        amount_claimed=claim.amount_claimed,
        currency=claim.currency,
        status=claim.status,
    )


def enquiry_subject(enquiry: MemberEnquiry) -> ConversationSubjectOut:
    """A question, reduced to what names a conversation.

    `about_claim` is nested as the SAME shape, so the client composes its label
    with the same helper it uses for a claim row — one place names a claim, on
    every surface. It is a reference: the question does not live on that claim,
    and the claim's own thread is untouched by it.
    """
    referenced = getattr(enquiry, "about_claim", None)
    return ConversationSubjectOut(
        kind="enquiry",
        id=enquiry.id,
        subject=enquiry.subject,
        topic=enquiry.topic,
        topic_label=topic_label(enquiry.topic),
        topic_urgent=topic_is_urgent(enquiry.topic),
        status=enquiry.status,
        about_claim=claim_subject(referenced) if referenced is not None else None,
    )


def subject_of(row: ConversationRow) -> ConversationSubjectOut:
    """Whichever kind of thread this is. Both surfaces go through here, so a
    question can never be named one way for a member and another for a
    broker."""
    if row.claim is not None:
        return claim_subject(row.claim)
    if row.enquiry is None:  # `_post` guarantees exactly one owner
        raise ValueError("a conversation must have a claim or a question")
    return enquiry_subject(row.enquiry)


def member_conversation_out(row: ConversationRow) -> ConversationOut:
    """A conversation as the MEMBER may see it — the last message goes through
    `member_message_out`, so a broker's name is substituted here too."""
    return ConversationOut(
        subject=subject_of(row),
        last_message=member_message_out(row.last),
        message_count=row.message_count,
        unread=row.unread,
    )


# ── Serializers, one per surface ──────────────────────────────────────────────


def member_message_out(msg: ClaimMessage) -> ClaimMessageOut:
    """What the MEMBER may see. `mine` marks their own replies (the thread
    aligns them differently); `unread` is about them, not about the broker."""
    return ClaimMessageOut(
        id=msg.id,
        claim_id=msg.claim_id,
        enquiry_id=msg.enquiry_id,
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
    )


def broker_message_out(msg: ClaimMessage) -> ClaimMessageOut:
    """What a BROKER sees: the real author, and unread meaning "the member
    wrote this and nobody here has opened it"."""
    return ClaimMessageOut(
        id=msg.id,
        claim_id=msg.claim_id,
        enquiry_id=msg.enquiry_id,
        author_type=msg.author_type,
        author_name=msg.author_name or (TEAM_NAME if msg.author_type == AUTHOR_SYSTEM else None),
        subject=msg.subject,
        body=msg.body,
        event=msg.event,
        created_at=msg.created_at,
        mine=msg.author_type != AUTHOR_MEMBER,
        unread=msg.author_type == AUTHOR_MEMBER and msg.broker_read_at is None,
    )
