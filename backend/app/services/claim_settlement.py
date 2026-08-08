"""The insurer leg of a claim: reference numbers, dispatch, payment, SLA.

Our claim used to end at the broker's decision. It does not end there in
practice — the broker sends the accepted claim to the insurer, the insurer pays
(or declines), and the gap between those two dates is the number a client asks
about. This module owns that leg.

Two design rules worth stating, because both were tempting to break:

**SLA counters are DERIVED, never stored.** "Days over deadline" on an unpaid
claim changes every night. A stored counter is wrong the morning after it is
written and there is no event to recompute it on, so the reports compute them
from the dates at read time.

**The document-receipt dates are derived too.** "First / Final Document Receive
Date" are just the earliest and latest `stored_documents.created_at` for the
claim — a real column would be a second, drifting copy of a fact the documents
already carry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_date, stamp_for_day
from app.core.clock import today as business_today
from app.models import Claim, Client, StoredDocument
from app.models.claim import (
    CLAIM_STATUS_PAID,
    CLAIM_STATUS_REJECTED,
    CLAIM_STATUS_SENT_TO_INSURER,
    SETTLED_STATUSES,
)

# Terminal states that carry no `paid_on` — the insurer's clock stopped, but no
# money moved. See `_insurer_clock_stop`.
_CLOSED_UNPAID_STATUSES = frozenset({CLAIM_STATUS_REJECTED})

# How long an insurer has, when the broker names no deadline. 30 days is the
# incumbent's own default (every "Deadline Date for Insurer" in CDL's live file
# is exactly 30 days after "Date Sent to Insurer").
DEFAULT_INSURER_TURNAROUND_DAYS = 30

_REF_PREFIX_FALLBACK = "CLM"
# Retries against a concurrent minter. Each loses only to a claim that took the
# exact number we read, so three is generous for a burst.
_MINT_ATTEMPTS = 3
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def reference_prefix(client: Client | None) -> str:
    """Short uppercase prefix for a client's claim references.

    Derived from the slug (or name) so the reference reads as the company's —
    `CDL-002577`. Falls back to a constant rather than an empty prefix: a bare
    number is not recognisable as a claim reference on a phone call.
    """
    raw = ""
    if client is not None:
        raw = (client.slug or client.name or "").strip()
    cleaned = _NON_ALNUM.sub("", raw).upper()
    return cleaned[:8] or _REF_PREFIX_FALLBACK


def mint_reference_no(db: Session, claim: Claim) -> str:
    """Allocate this claim's reference, once.

    Idempotent: an already-referenced claim keeps its number. Resubmission after
    `needs_info` runs back through `submit_claim`, and a claim whose reference
    changed between submissions is one the member can no longer quote and the
    broker can no longer find in the insurer's ledger.

    The sequence is per CLIENT and derived from the highest number already
    issued rather than a counter table — there is no separate row to fall out of
    step with the claims, and a gap (a deleted draft) is harmless. Numbers are
    never reused: the max only moves up.

    **Read-max-then-write races**, and two members submitting at the same moment
    is ordinary during a portal rollout (and guaranteed across App Service
    instances, which share the database but not this process). A duplicate here
    is expensive and silent: the reference is the key a broker reconciles
    against the insurer's ledger. The unique index
    (`ix_claims_reference_no`) is what actually prevents it — the retry below
    only turns the resulting IntegrityError into a correct second attempt
    instead of a failed submission.
    """
    if claim.reference_no:
        return claim.reference_no

    client = db.get(Client, claim.client_id)
    prefix = reference_prefix(client)
    for _attempt in range(_MINT_ATTEMPTS):
        candidate = f"{prefix}-{_next_sequence(db, prefix):06d}"
        savepoint = db.begin_nested()
        try:
            claim.reference_no = candidate
            db.flush()
        except IntegrityError:
            savepoint.rollback()
            claim.reference_no = None
            # Retry ONLY when this candidate was genuinely taken between our
            # read and our write. The flush covers every pending change in the
            # transaction, so an unrelated constraint failure would otherwise be
            # swallowed and reported as "could not allocate a reference" —
            # three times over, hiding the real fault.
            if not _reference_taken(db, candidate):
                raise
            continue
        savepoint.commit()
        return candidate
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Could not allocate a claim reference. Please try again.",
    )


def _reference_taken(db: Session, reference_no: str) -> bool:
    return (
        db.scalar(
            select(func.count(Claim.id)).where(Claim.reference_no == reference_no)
        )
        or 0
    ) > 0


def _next_sequence(db: Session, prefix: str) -> int:
    """One past the highest sequence issued under this PREFIX.

    Compares as TEXT on a zero-padded 6-digit tail, so `MAX` orders correctly
    right up to 999,999 references for one prefix.

    **Scoped by prefix, NOT by client — because that is what the uniqueness is
    on.** `ix_claims_reference_no` is unique across the whole schema, while a
    prefix is only the first 8 alphanumerics of a company's slug, so two related
    companies in one firm collide: "CDL Holdings Pte Ltd" and "CDL Holding
    Group" both reduce to `CDLHOLDI`. A client-scoped max returned 1 for the
    second company no matter how many references the first had already issued,
    so its first claim proposed a number that was taken, the retry recomputed
    the identical candidate, and submission 503'd — permanently, for every claim
    that company ever filed. Reading the max the constraint actually governs
    makes the retry converge instead of repeating itself.

    The two companies then share one ascending series. That is the right
    trade: a reference is a lookup key, not a per-company counter, and it stays
    unique and monotonic. (`prefix` is alphanumeric by construction, so it can
    carry no LIKE wildcard.)
    """
    highest = db.scalar(
        select(func.max(Claim.reference_no)).where(
            Claim.reference_no.like(f"{prefix}-%"),
        )
    )
    if highest:
        tail = str(highest).rsplit("-", 1)[-1]
        if tail.isdigit():
            return int(tail) + 1
    return 1


@dataclass(frozen=True)
class DocumentDates:
    first: datetime | None
    final: datetime | None


def document_dates(db: Session, claim_ids: list[str]) -> dict[str, DocumentDates]:
    """Earliest + latest document upload per claim, in one query.

    These ARE the "First / Final Document Receive Date" columns. Computed here
    rather than stored so they cannot drift from the documents themselves —
    a late upload moves the final date, which is exactly what the servicer SLA
    should measure against.
    """
    if not claim_ids:
        return {}
    rows = db.execute(
        select(
            StoredDocument.entity_id,
            func.min(StoredDocument.created_at),
            func.max(StoredDocument.created_at),
        )
        .where(
            StoredDocument.entity_type == "claim",
            StoredDocument.entity_id.in_(claim_ids),
        )
        .group_by(StoredDocument.entity_id)
    ).all()
    return {cid: DocumentDates(first=lo, final=hi) for cid, lo, hi in rows}


def _as_date(value: datetime | date | None) -> date | None:
    """A stored value as the calendar date a broker reads it on.

    Through `clock.business_date`, never a bare `.date()`: every date this
    returns is subtracted from another date below to produce an SLA day count,
    and `_insurer_clock_stop` supplies the other end from `business_today()`. A
    UTC-derived start against a Singapore-derived end is off by one for any
    instant after 16:00 UTC. `date` columns (`paid_on`, `insurer_deadline_on`)
    are already calendar dates and pass straight through.

    Dispatch dates a broker STATES are written by `stamp_for_day` (noon UTC),
    so they read back as the day stated under either convention.
    """
    if value is None:
        return None
    return business_date(value) if isinstance(value, datetime) else value


def servicer_days(claim: Claim, docs: DocumentDates | None) -> int | None:
    """Days we held the claim: first document received → our decision.

    None until the claim is decided — an open claim has no elapsed servicing
    time, it has an age, and reporting the two under one heading is how a queue
    starts looking like a backlog it isn't.
    """
    start = _as_date(docs.first if docs else None) or _as_date(claim.submitted_at)
    end = _as_date(claim.decided_at)
    if start is None or end is None:
        return None
    return (end - start).days


def _insurer_clock_stop(claim: Claim) -> date:
    """The date the insurer's clock stopped, or today while it is still running.

    Payment stops it. So does a REJECTION — an insurer declining after we
    accepted is an outcome, not an unanswered claim, and `paid_on` is never set
    on one. Without this branch a declined claim's "Days Over Deadline" climbs
    every night forever and it sits at the top of the overdue list a broker
    works, permanently, having already been settled.

    Falls back to the decision timestamp, then today: a terminal claim always
    has one, but a hand-edited row might not, and a wrong-by-a-day figure beats
    a crash in a report.
    """
    if claim.paid_on is not None:
        return claim.paid_on
    if claim.status in _CLOSED_UNPAID_STATUSES:
        return _as_date(claim.decided_at) or business_today()
    # Business date, not the UTC one (`core/clock.py`): on a UTC server every
    # open claim's age would tick over at 8am Singapore, so the overdue list a
    # broker works disagreed with itself for the first hour of the day — and it
    # has to be the same calendar `_as_date` reads the other end in.
    return business_today()


def insurer_days(claim: Claim) -> int | None:
    """Days the insurer held it: sent → settled, or sent → today while open.

    Deliberately keeps counting on an OPEN claim. A blank here would make an
    overdue claim indistinguishable from one that was never sent, and chasing
    overdue claims is what the column is for.
    """
    start = _as_date(claim.sent_to_insurer_at)
    if start is None:
        return None
    return (_insurer_clock_stop(claim) - start).days


def days_over_deadline(claim: Claim) -> int | None:
    """Signed days past the insurer's deadline; negative = still in time."""
    if claim.insurer_deadline_on is None:
        return None
    return (_insurer_clock_stop(claim) - claim.insurer_deadline_on).days


def send_to_insurer(
    db: Session,
    claim: Claim,
    *,
    user_id: str | None,
    sent_on: date | None = None,
    deadline_on: date | None = None,
    turnaround_days: int | None = None,
) -> Claim:
    """Record dispatch of an accepted claim. Caller commits.

    The transition itself is enforced by `assert_transition`; this fills the
    dates. When no deadline is given one is derived from the dispatch date so
    the SLA columns are never silently blank — an unbounded claim is one nobody
    chases.
    """
    now = datetime.now(UTC)
    if sent_on is not None:
        # A STATED date, widened by `stamp_for_day` — never by the current
        # wall clock. Combining it with `now.timetz()` made the stored
        # instant's calendar date depend on the hour it was keyed: a dispatch
        # recorded after 16:00 UTC read as the FOLLOWING day to any reader in
        # Singapore, silently moving the start of the insurer's SLA clock.
        claim.sent_to_insurer_at = stamp_for_day(sent_on)
    else:
        claim.sent_to_insurer_at = now
    claim.sent_to_insurer_by = user_id
    if deadline_on is not None:
        claim.insurer_deadline_on = deadline_on
    else:
        days = turnaround_days or DEFAULT_INSURER_TURNAROUND_DAYS
        claim.insurer_deadline_on = _as_date(claim.sent_to_insurer_at) + timedelta(
            days=days
        )
    claim.status = CLAIM_STATUS_SENT_TO_INSURER
    return claim


def record_payment(
    db: Session,
    claim: Claim,
    *,
    paid_on: date,
    amount: float | None = None,
) -> Claim:
    """Record the insurer's payment. Caller commits.

    ``amount`` defaults to what we approved. It is stored SEPARATELY from
    `amount_approved` rather than overwriting it: the difference between the two
    is a shortfall, and a reconciliation report that cannot see one has no
    reason to exist.
    """
    claim.paid_on = paid_on
    claim.payment_amount = (
        amount if amount is not None else claim.amount_approved
    )
    claim.status = CLAIM_STATUS_PAID
    return claim


def is_settled(claim: Claim) -> bool:
    return claim.status in SETTLED_STATUSES


# The claim's dates a broker may CORRECT after the fact, as request-field names.
# `sent_to_insurer_on` is a date on the request and a timestamp on the row.
SETTLEMENT_AMENDMENTS = frozenset(
    {"sent_to_insurer_on", "insurer_deadline_on", "paid_on", "payment_amount"}
)

# The two that describe money HAVING MOVED, as opposed to the dispatch that
# started the insurer's clock. Correctable only on a claim recorded as paid —
# see `assert_settlement_amendable`.
_PAYMENT_AMENDMENTS = frozenset({"paid_on", "payment_amount"})

# Request-field name → the column it writes, for callers that need to snapshot
# the claim before an amendment (the audit trail does).
AMENDMENT_COLUMNS = {"sent_to_insurer_on": "sent_to_insurer_at"}


def was_dispatched(claim: Claim) -> bool:
    """Whether this claim ever went to the insurer.

    The STATUS is not enough on its own: `sent_to_insurer → rejected` is a real
    transition (the insurer declining after we accepted), and such a claim was
    dispatched, has a deadline, and is exactly the one whose recorded dates a
    broker may need to correct. Gating on `status in {sent_to_insurer, paid}`
    alone refused it with "…once the claim has been sent to the insurer", which
    is not only unhelpful but false.
    """
    return (
        claim.sent_to_insurer_at is not None
        or claim.status in (CLAIM_STATUS_SENT_TO_INSURER, CLAIM_STATUS_PAID)
    )


def assert_settlement_amendable(
    claim: Claim, present: frozenset[str] | set[str]
) -> None:
    """Refuse corrections the claim's state does not admit. Raises 409.

    Two rules, and the second is the load-bearing one:

    - **Nothing before dispatch.** Backfilling a dispatch date onto a draft
      would invent a history the SLA counters then report on.
    - **A payment can only be corrected on a claim recorded as PAID.** Writing
      `paid_on` onto a claim still `sent_to_insurer` does not move the status,
      but `_insurer_clock_stop` reads `paid_on` FIRST — so the SLA counters
      freeze, the claim drops off the overdue list a broker works and out of
      the "Nd over" badge, while it is still with the insurer, still counted as
      pending against the member's limit, and no money has arrived. An unpaid
      claim quietly leaving the chase list is the worst failure this module has.
    """
    if not present:
        return
    if not was_dispatched(claim):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Settlement dates can only be corrected once the claim has been "
            "sent to the insurer.",
        )
    if present & _PAYMENT_AMENDMENTS and claim.status != CLAIM_STATUS_PAID:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The payment date and amount can only be corrected on a claim "
            "recorded as paid. Use Record payment to settle it.",
        )


def apply_settlement_amendment(claim: Claim, body, present: frozenset[str] | set[str]):
    """Correct the recorded settlement dates without touching the status.

    `send_to_insurer` and `record_payment` are TRANSITIONS, offered only from
    the single status each is legal in. That leaves no way back: a claim that
    reached `paid` without passing through dispatch — a LOG case settled
    outside the flow, a migrated row, a date keyed in wrongly — has lost the
    control that sets these, permanently, and the SLA columns that read them
    stay blank forever with nothing in the product able to fill them.

    This is the amendment path. It writes the same fields and deliberately does
    NOT move the status: re-running the transition would repost the member's
    "your claim has been paid" notice for a typo correction.

    Caller commits. Ordering is validated against the EFFECTIVE values (what
    the claim will hold after the merge), not just what this request carried —
    a partial update that moves only the deadline is exactly how it comes to
    precede a dispatch date already on the row.
    """
    if not present:
        return

    def effective(field: str, current):
        return getattr(body, field) if field in present else current

    sent = effective("sent_to_insurer_on", _as_date(claim.sent_to_insurer_at))
    deadline = effective("insurer_deadline_on", claim.insurer_deadline_on)
    paid = effective("paid_on", claim.paid_on)

    # A cleared date input sends null, so this is one keystroke away on the
    # form. Clearing the dispatch date of a claim that IS with the insurer
    # leaves it reporting itself as dispatched with no dispatch date:
    # `insurer_days` goes blank while `days_over_deadline` keeps counting
    # against a deadline nothing now explains. Correcting a date is legitimate;
    # deleting it out from under the status is not.
    if sent is None and was_dispatched(claim):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This claim has been sent to the insurer, so it must keep a "
            "dispatch date. Correct the date rather than clearing it.",
        )

    if sent is not None and deadline is not None and deadline < sent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The insurer deadline cannot precede the date sent to the insurer.",
        )
    if sent is not None and paid is not None and paid < sent:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "The payment date cannot precede the date sent to the insurer.",
        )

    if "sent_to_insurer_on" in present:
        # Same widening as `send_to_insurer` — only the DATE was ever stated,
        # and `stamp_for_day` is what makes it read back as that date.
        claim.sent_to_insurer_at = (
            stamp_for_day(sent) if sent is not None else None
        )
    if "insurer_deadline_on" in present:
        claim.insurer_deadline_on = deadline
    if "paid_on" in present:
        claim.paid_on = paid
    if "payment_amount" in present:
        claim.payment_amount = body.payment_amount
