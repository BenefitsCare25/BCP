"""Insurance/flex claim submitted by a portal member (tenant table).

Status machine (single source of truth: `VALID_TRANSITIONS`):

    draft ──submit──▶ submitted ──pipeline──▶ ai_review_pending ─▶ ai_verified
                          │                        │               ai_flagged
                          │  (pipeline error: claim returns to "submitted",
                          │   the review row records the failure)
                          ▼
    broker decision (from submitted / ai_* / needs_info):
        approve → approved              reject → rejected (terminal)
        needs_info → member edits + resubmits → submitted
    draft → member delete (row removed)

    settlement (the insurer leg — see `services/claim_settlement.py`):

        approved ──send-to-insurer──▶ sent_to_insurer ──payment──▶ paid
                                            └──────────────▶ rejected

`approved` is therefore NOT terminal: it means *we* accepted the claim, which
is the start of the insurer's part, not the end of the claim. Deliberately no
separate `pending_documents` state — that is exactly `needs_info`, and a second
spelling of one state is how two queues start disagreeing about the same claim.
The incumbent's label is applied at the report boundary only.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSON, Base, TimestampMixin, new_uuid

CLAIM_STATUS_DRAFT = "draft"
CLAIM_STATUS_SUBMITTED = "submitted"
CLAIM_STATUS_AI_REVIEW_PENDING = "ai_review_pending"
CLAIM_STATUS_AI_VERIFIED = "ai_verified"
CLAIM_STATUS_AI_FLAGGED = "ai_flagged"
CLAIM_STATUS_NEEDS_INFO = "needs_info"
CLAIM_STATUS_APPROVED = "approved"
CLAIM_STATUS_REJECTED = "rejected"
CLAIM_STATUS_SENT_TO_INSURER = "sent_to_insurer"
CLAIM_STATUS_PAID = "paid"

CLAIM_STATUSES = frozenset(
    {
        CLAIM_STATUS_DRAFT,
        CLAIM_STATUS_SUBMITTED,
        CLAIM_STATUS_AI_REVIEW_PENDING,
        CLAIM_STATUS_AI_VERIFIED,
        CLAIM_STATUS_AI_FLAGGED,
        CLAIM_STATUS_NEEDS_INFO,
        CLAIM_STATUS_APPROVED,
        CLAIM_STATUS_REJECTED,
        CLAIM_STATUS_SENT_TO_INSURER,
        CLAIM_STATUS_PAID,
    }
)

# States whose position in the machine is OWNED by the AI review — it either
# has the claim or has just answered on it.
#
# An amendment invalidates any verdict on the claim (the review compares
# documents against `form_fields`, so once those change it describes a claim
# that no longer exists), and these are exactly the states that then have to
# fall back to plain `submitted` for manual review. `needs_info` and `submitted`
# do not: the review is superseded either way, but their status already says
# what is true.
AI_REVIEW_STATUSES = frozenset(
    {
        CLAIM_STATUS_AI_REVIEW_PENDING,
        CLAIM_STATUS_AI_VERIFIED,
        CLAIM_STATUS_AI_FLAGGED,
    }
)

# States a broker may decide from (approve / reject / needs_info).
DECIDABLE_STATUSES = frozenset(
    {
        CLAIM_STATUS_SUBMITTED,
        CLAIM_STATUS_AI_REVIEW_PENDING,
        CLAIM_STATUS_AI_VERIFIED,
        CLAIM_STATUS_AI_FLAGGED,
        CLAIM_STATUS_NEEDS_INFO,
    }
)

# States where the money is COMMITTED — we have accepted the claim, whatever
# stage of the insurer leg it has reached.
#
# **This set, not `== CLAIM_STATUS_APPROVED`, is what utilization must test.**
# Every one of these has an `amount_approved` that is spoken for, so a claim
# that has been sent to the insurer or already paid still consumes the limit.
# Comparing to `approved` alone made settlement RESTORE a member's limit: the
# claim fell out of the approved sum and back into "pending", which
# `utilization` reports separately and never subtracts.
SETTLED_STATUSES = frozenset(
    {
        CLAIM_STATUS_APPROVED,
        CLAIM_STATUS_SENT_TO_INSURER,
        CLAIM_STATUS_PAID,
    }
)

# States in which the CLAIMANT may still change their own claim — edit its
# fields, add or remove documents, submit it.
#
# Derived BY UNION from `DECIDABLE_STATUSES`, not spelled out: "a member may
# correct their claim for exactly as long as a broker has not yet decided it" is
# ONE fact, and a second spelling of it starts disagreeing the day a
# pre-decision status is added. Same discipline as `PENDING_STATUSES` below,
# which is derived by subtraction for the same reason.
#
# It used to be `{draft, needs_info}`, i.e. document-attachment only, because
# there was no field-edit endpoint at all. Widening it opens BOTH — and that is
# deliberate: a claim whose amount a member may correct but whose receipt they
# may not replace is incoherent, since the wrong figure and the wrong receipt
# are the same mistake. See `docs/CLAIM_AMENDMENT_PLAN.md`.
MEMBER_EDITABLE_STATUSES = DECIDABLE_STATUSES | {CLAIM_STATUS_DRAFT}

# States from which the MEMBER may (re)send the claim to us. A NARROWER
# question than editability: a claim already in review is theirs to correct but
# not theirs to send again.
#
# **This is deliberately NOT derived from `VALID_TRANSITIONS[status] ∋
# submitted`**, which now admits three states it must not: `ai_verified` and
# `ai_flagged` gained that edge so an AMENDMENT could knock a stale verdict back
# to manual review, and `ai_review_pending → submitted` has always been the
# pipeline's own fault path. None of the three is a member pressing Send, and
# reading the transition table here would let one POST /submit on a claim
# already in review — re-notifying the member and resetting its status.
MEMBER_SUBMITTABLE_STATUSES = frozenset(
    {CLAIM_STATUS_DRAFT, CLAIM_STATUS_NEEDS_INFO}
)

# States that count against limits/duplicate checks ("live" claims).
LIVE_STATUSES = frozenset(CLAIM_STATUSES - {CLAIM_STATUS_DRAFT, CLAIM_STATUS_REJECTED})

# Live but not yet settled — a claim still in flight, whose amount may or may
# not end up consuming the limit.
#
# Derived BY SUBTRACTION, so a status added above lands here by default — right
# for a new in-flight state and catastrophically wrong for a new settled one:
# the claim would drop out of `approved` (subtracted from the limit) into
# `pending` (reported beside it and never subtracted), handing the member back a
# limit they have already spent. Add settled states to `SETTLED_STATUSES`.
#
# It lives HERE rather than in `utilization` because it is a fact about the
# status model, and three subsystems now ask the question — utilization,
# duplicate-invoice checks, and `member_access` (a leaver keeps read access
# while a claim of theirs is still open). `utilization` re-exports the name.
PENDING_STATUSES = frozenset(LIVE_STATUSES - SETTLED_STATUSES)

# Hospital sector, as the insurer classifies it. Drives which invoice type the
# document classifier expects (govt vs private) and appears on the claims
# reports; see `services/claim_doc_types.py`.
HOSPITAL_TYPE_GOVERNMENT = "government"
HOSPITAL_TYPE_PRIVATE = "private"
HOSPITAL_TYPES = frozenset({HOSPITAL_TYPE_GOVERNMENT, HOSPITAL_TYPE_PRIVATE})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    CLAIM_STATUS_DRAFT: frozenset({CLAIM_STATUS_SUBMITTED}),
    CLAIM_STATUS_SUBMITTED: frozenset(
        {
            CLAIM_STATUS_AI_REVIEW_PENDING,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
        }
    ),
    CLAIM_STATUS_AI_REVIEW_PENDING: frozenset(
        {
            CLAIM_STATUS_AI_VERIFIED,
            CLAIM_STATUS_AI_FLAGGED,
            # Pipeline failure falls back to plain "submitted" (manual review).
            CLAIM_STATUS_SUBMITTED,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            # Self-transition: broker rerun-review recovers a claim whose
            # background task died before persisting anything (BackgroundTasks
            # are in-process and non-durable — a deploy/crash in the window
            # after submit strands the claim here otherwise).
            CLAIM_STATUS_AI_REVIEW_PENDING,
        }
    ),
    CLAIM_STATUS_AI_VERIFIED: frozenset(
        {
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            # Broker rerun-review re-enters the pipeline.
            CLAIM_STATUS_AI_REVIEW_PENDING,
            # A MEMBER AMENDMENT invalidates the verdict. A review is a
            # statement about a specific set of claimed values — it compares
            # the documents against `form_fields` — so once those values
            # change it describes a claim that no longer exists. The claim
            # falls back to plain `submitted` (manual review) and the review
            # row is superseded, which is the same landing the pipeline's own
            # fault path uses. Deliberately NOT an automatic re-run: a member
            # can amend repeatedly, so auto-rerun would make an edit loop an
            # AI-spend loop. See `docs/CLAIM_AMENDMENT_PLAN.md`.
            CLAIM_STATUS_SUBMITTED,
        }
    ),
    CLAIM_STATUS_AI_FLAGGED: frozenset(
        {
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
            CLAIM_STATUS_NEEDS_INFO,
            CLAIM_STATUS_AI_REVIEW_PENDING,
            # Member amendment — see AI_VERIFIED above.
            CLAIM_STATUS_SUBMITTED,
        }
    ),
    CLAIM_STATUS_NEEDS_INFO: frozenset(
        {
            CLAIM_STATUS_SUBMITTED,
            CLAIM_STATUS_APPROVED,
            CLAIM_STATUS_REJECTED,
        }
    ),
    CLAIM_STATUS_APPROVED: frozenset({CLAIM_STATUS_SENT_TO_INSURER}),
    CLAIM_STATUS_SENT_TO_INSURER: frozenset(
        {
            CLAIM_STATUS_PAID,
            # The insurer declining after we accepted is a real outcome, and
            # the member's limit must be released when it happens — which
            # `rejected` does (it is outside LIVE_STATUSES) and no other state
            # would.
            CLAIM_STATUS_REJECTED,
        }
    ),
    CLAIM_STATUS_PAID: frozenset(),
    CLAIM_STATUS_REJECTED: frozenset(),
}

CLAIM_KIND_INSURED = "insured"
CLAIM_KIND_FLEX = "flex"

# ── Case type ────────────────────────────────────────────────────────────────
#
# LOG is another claim CATEGORY, not another workflow: a LOG case runs the same
# status machine, the same broker decision and the same utilization maths as a
# reimbursement claim. What differs is who creates it (an assessor, from an
# emailed request), which intake rules apply (see `services/log_cases.py`) and
# who may see it (see `origin` below).
#
# This is a TYPED column and not the free-text `claim_type` on purpose:
# `claim_type` arrives FROM THE MEMBER in `ClaimCreateIn`, so a string check
# would let a member classify their own claim as a broker-internal LOG case and
# skip the document rules. Only broker endpoints write this column.
CASE_TYPE_CLAIM = "claim"
CASE_TYPE_LOG = "log"
CASE_TYPES = frozenset({CASE_TYPE_CLAIM, CASE_TYPE_LOG})

# The `claim_type` display value a LOG case carries (the broker queue's "Claim"
# column). Broker vocabulary — members never see the string "LOG".
LOG_CLAIM_TYPE = "LOG"

# ── Amendment actor ──────────────────────────────────────────────────────────
#
# Who last corrected the claim (`Claim.amended_by`). Deliberately the SURFACE
# and not a user id: the only question anyone asks of it is "did this move under
# me?", and the audit trail already records exactly who did it.
AMENDED_BY_MEMBER = "member"
AMENDED_BY_BROKER = "broker"

# ── Origin ───────────────────────────────────────────────────────────────────
#
# Who put this row here, which is a DIFFERENT question from what kind of case it
# is — and it is the one the portal filters on. Filtering the member's claim list
# on `case_type` would make a member's own submission vanish from their portal
# the moment an assessor reclassified it as a LOG case; filtering on `origin`
# hides only the cases they never knew about. See `api/v1/portal_claims.py`.
ORIGIN_PORTAL = "portal"
ORIGIN_BROKER = "broker"
ORIGINS = frozenset({ORIGIN_PORTAL, ORIGIN_BROKER})

# States a case may be RECLASSIFIED in. Identical to `DECIDABLE_STATUSES` by
# construction rather than by coincidence: a case can be reclassified for
# exactly as long as it can still be decided. A decided claim is not
# reclassifiable — the money is settled, and relabelling it rewrites history
# rather than correcting it. A draft is excluded because it is still the
# member's to edit.
RELABELLABLE_STATUSES = DECIDABLE_STATUSES


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"
    __table_args__ = (
        Index(
            "ix_claims_employee_year_status",
            "employee_id",
            "policy_year_id",
            "status",
        ),
        # The broker queue and the employee-level card both filter on case type
        # within a policy year.
        #
        # Declared HERE and not only in the migration: `db/tenancy.sync_firm_schema`
        # reconciles indexes from MODEL METADATA (`for idx in tbl.indexes`), and
        # alembic runs against `public` alone. A migration-only index therefore
        # lands on `public.claims` — which holds no rows on Postgres — while
        # every real query runs in `firm_<id>` without it.
        Index("ix_claims_year_case_type", "policy_year_id", "case_type"),
        # UNIQUE — `claim_settlement.mint_reference_no` reads the current max and
        # writes one past it, and that races between concurrent submissions.
        # Declared HERE for the same reason as the index above: `sync_firm_schema`
        # reconciles from MODEL METADATA, so a migration-only constraint would
        # guard `public.claims` (empty on Postgres) while every real claim is
        # written to `firm_<id>` without it — i.e. the guard would be absent in
        # exactly the environment that needs it. NULLs are exempt from uniqueness
        # on both dialects, so drafts and pre-existing claims are unaffected.
        Index("ix_claims_reference_no", "reference_no", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_year_id: Mapped[str] = mapped_column(
        ForeignKey("policy_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[str] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The claimant when claiming for a covered dependant; NULL = the member.
    dependant_id: Mapped[str | None] = mapped_column(
        ForeignKey("dependants.id", ondelete="SET NULL"), nullable=True
    )
    claim_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CLAIM_KIND_INSURED
    )
    # Claim category — see CASE_TYPE_* above. Server-defaulted so every existing
    # row reads as an ordinary claim, which is what they all are.
    case_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CASE_TYPE_CLAIM,
        server_default=CASE_TYPE_CLAIM,
    )
    # Who created the row — see ORIGIN_* above. The portal's visibility filter.
    origin: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ORIGIN_PORTAL,
        server_default=ORIGIN_PORTAL,
    )
    # Broker author of a broker-created case (mirror of submitted_by_member_id).
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Provenance of an emailed request + the reclassification trail:
    # {received_via, received_on, requested_by, conversions: [...]}. Read
    # DEFENSIVELY — untyped JSON, and legacy rows carry None.
    intake_meta: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)
    # Insured claims: which coverage line + SOB benefit item the claim draws on.
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    benefit_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Flex claims: which claimable scheme category.
    flex_category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # GHS-family claims name a sub-claim type (see claim_intake.GHS_SUB_TYPES).
    sub_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Specialist claims: "first" or "follow_up" (claim_intake.VISIT_TYPES) —
    # drives the referral-letter requirement (first visit must attach one;
    # follow-up reuses the latest letter on file).
    visit_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Specialist claims: the member-level referral letter this claim rides on
    # (stored_documents row with entity_type="referral"). Plain string, not an
    # FK — referral letters are member-owned and never cascade with the claim.
    referral_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    incurred_date: Mapped[date] = mapped_column(Date, nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The treating doctor. Required only on pre-/post-hospitalisation consults
    # (`claim_intake.requires_doctor_name`) — the insurer matches the consult to
    # the admission through it — and extracted from the bill at intake.
    doctor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Receipt / tax-invoice number the member transcribes — cross-checked against
    # the uploaded documents by the AI review (see field_maps.py).
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Free-text member note (not a document-matched field).
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_claimed: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="SGD")
    amount_converted: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_approved: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CLAIM_STATUS_DRAFT, index=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Member-entered claim-form snapshot the AI review compares documents against.
    #
    # RE-SNAPSHOTTED on amendment, so the original statement is overwritten. The
    # `AuditLog` before/after is the history — one store, not two; a parallel
    # copy here would be a second thing to keep true.
    form_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON(), nullable=True)

    # ── Amendment ────────────────────────────────────────────────────────────
    #
    # Bumped by EVERY amendment, member-side or broker-side. It is a concurrency
    # guard, not a counter anybody displays: a broker reads $150 in the queue,
    # the member corrects it to $105, and the broker then approves a figure that
    # is no longer on the claim. `ClaimDecisionIn.expected_revision` carries the
    # value the broker actually read and 409s on a mismatch.
    #
    # An optimistic guard rather than a lock, because a lock needs an owner, a
    # timeout and a release path, and gets stuck holding a claim the moment an
    # assessor closes the tab.
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # When the claim was last amended.
    #
    # A server-set instant (the moment the edit happened), not a date anyone
    # typed, so `datetime.now(UTC)` is right here and `stamp_for_day` is not.
    amended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # WHO made that last amendment — `member` or `broker`.
    #
    # It exists because the queue's "Amended" chip means one specific thing:
    # this claim moved UNDER the assessor. Three writers stamp `amended_at` —
    # the member's edit, the member adding or removing a document, and the
    # broker's own correction — so a chip gated on the timestamp alone flags an
    # assessor's own edit back at them the instant they save it, which is the
    # one reading the badge must never have. Nothing else reads this column;
    # `revision` remains the concurrency guard for every actor alike.
    amended_by: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ── Human-quotable reference ─────────────────────────────────────────────
    #
    # Minted once at SUBMIT (see `claim_settlement.mint_reference_no`) and never
    # reused. This is the string a member quotes to support and the key a broker
    # reconciles against the insurer's ledger — the row id is a uuid nobody can
    # read over the phone. Nullable because a draft has none: a claim that was
    # never submitted has nothing to reference.
    # Indexed + uniquely constrained by the table-level `ix_claims_reference_no`
    # above — NOT `index=True` here, which would add a second, non-unique index.
    reference_no: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ── Insurer settlement leg ───────────────────────────────────────────────
    sent_to_insurer_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_to_insurer_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # When the insurer's turnaround expires. A DATE, not a timestamp: it is a
    # business deadline negotiated in days, and the SLA counters compare dates.
    insurer_deadline_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The insurer's OWN payment date, transcribed from their advice — not the
    # moment a broker keyed it in, which is why this is a date and not a
    # server-set timestamp.
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # What the insurer actually paid. Distinct from `amount_approved`: a
    # shortfall between the two is the whole reason a reconciliation report
    # exists, so collapsing them would hide it.
    payment_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Clinical / assessment detail ─────────────────────────────────────────
    # Sector as the insurer classifies it (HOSPITAL_TYPE_*). Also the tie-break
    # the document classifier uses between govt and private invoice types.
    hospital_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    admission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    discharge_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Payroll treatment of the reimbursement. Nullable for storage only — NULL
    # means "nobody has changed it from the default", and the default IS No:
    # that is the ordinary treatment of a medical reimbursement, it is what the
    # assessment form offers (Yes/No, defaulting No) and what `claims_reports.
    # _flag` prints. This was a tri-state where NULL printed blank; once the
    # form defaulted to No, blank became a second answer to the same question.
    taxable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cpf_claimable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Broker-side note. Kept apart from `remarks` (the MEMBER's note) because
    # the member can read theirs back and must never read this one.
    admin_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


def member_visible_claims():
    """The SQL condition selecting claims a MEMBER may see: their own
    submissions, never a case an assessor recorded from an email.

    **Every member-facing query over `claims` must apply this**, including the
    ones that reach claims through a JOIN (the message inbox, the unread count).
    It lives here rather than in one router because it was originally written in
    `portal_claims` alone — and the message surfaces, which hang off the very
    same rows, silently kept serving the cases the claim list had started
    hiding. `services/claims.load_member_claim` is the point-load counterpart.
    """
    return Claim.origin == ORIGIN_PORTAL
