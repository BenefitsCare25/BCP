"""Pydantic schemas for the claims module (portal member + broker surfaces)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Documents ─────────────────────────────────────────────────────────────────


# A required-document upload slot on the claim form / claim detail.
class DocSlotOut(BaseModel):
    key: str
    label: str


class StoredDocumentOut(_Base):
    id: str
    file_name: str
    # Which required-document slot this upload fills (claim_intake.DOC_SLOT
    # keys); None = untagged/additional document.
    doc_type: str | None = None
    mime_type: str | None = None
    size_bytes: int
    sha256: str
    created_at: datetime


# ── Claims ────────────────────────────────────────────────────────────────────


class ClaimCreateIn(BaseModel):
    claim_kind: str = Field(pattern="^(insured|flex)$")
    # insured claims (the Benefit/SOB-item picker was removed from the form —
    # benefit attribution is the broker's call at review time)
    product_code: str | None = Field(default=None, max_length=64)
    # flex claims
    flex_category_name: str | None = Field(default=None, max_length=255)
    claim_type: str = Field(min_length=1, max_length=64)
    # Required for GHS-family products; validated against the intake profile.
    sub_type: str | None = Field(default=None, max_length=64)
    # Specialist claims: "first" | "follow_up" (drives the referral rule).
    visit_type: str | None = Field(default=None, max_length=16)
    incurred_date: date
    provider_name: str = Field(min_length=2, max_length=255)
    invoice_number: str = Field(min_length=1, max_length=128)
    # Required for pre-/post-hospitalisation consults only (validated against
    # the intake profile — `claim_intake.requires_doctor_name`).
    doctor_name: str | None = Field(default=None, max_length=255)
    diagnosis: str | None = Field(default=None, max_length=512)
    remarks: str | None = Field(default=None, max_length=500)
    amount_claimed: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="SGD", min_length=3, max_length=8)
    dependant_id: str | None = None
    # Specialist claims: a previously uploaded referral letter, or an explicit
    # "not applicable" declaration (recorded for the broker + AI review).
    referral_document_id: str | None = None
    referral_not_applicable: bool = False


# Amendable fields the CLAIM cannot hold as NULL. Sending an explicit `null` for
# one of these is a request to clear a column the model requires, which would
# either 500 on flush or store a claim nothing downstream can render. Absence
# means "don't touch"; `null` means "clear it", and for these two there is no
# difference worth the ambiguity.
_NOT_CLEARABLE = frozenset(
    {
        "claim_kind",
        "claim_type",
        "incurred_date",
        "provider_name",
        "invoice_number",
        "amount_claimed",
        "currency",
    }
)


# Fields that STEER an amendment rather than being part of it. They must not
# count towards "did this request change anything", or a body carrying nothing
# but a reason would bump the revision and announce an edit that never happened.
_AMEND_CONTROL_FIELDS = frozenset({"expected_revision", "reason"})


class _ClaimAmendBase(BaseModel):
    """The claim FACTS an amendment may carry, shared by both surfaces.

    A PARTIAL update driven by `model_fields_set`, for the same reason
    `ClaimAssessmentIn` is: an edit sheet that touches one control must not
    blank everything else. Absence = leave alone; an explicit `null` = clear
    (which is how a claim stops being for a dependant, or loses a diagnosis).

    Every value is re-validated by `claims.validate_claim_facts` over the MERGED
    claim, so an amendment can only ever produce a claim that would pass
    submit — including the duplicate-invoice refusal, which has no override on
    either surface.

    The two subclasses differ only at the edges, and each edge is deliberate:
    `remarks` is the MEMBER's own note (a broker rewriting it is forgery) and
    `benefit_key` is BROKER attribution (never the member's). See
    `docs/CLAIM_AMENDMENT_PLAN.md` §3.
    """

    claim_kind: str | None = Field(default=None, pattern="^(insured|flex)$")
    product_code: str | None = Field(default=None, max_length=64)
    flex_category_name: str | None = Field(default=None, max_length=255)
    claim_type: str | None = Field(default=None, min_length=1, max_length=64)
    sub_type: str | None = Field(default=None, max_length=64)
    visit_type: str | None = Field(default=None, max_length=16)
    incurred_date: date | None = None
    provider_name: str | None = Field(default=None, min_length=2, max_length=255)
    invoice_number: str | None = Field(default=None, min_length=1, max_length=128)
    doctor_name: str | None = Field(default=None, max_length=255)
    diagnosis: str | None = Field(default=None, max_length=512)
    amount_claimed: float | None = Field(default=None, gt=0, le=1_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    dependant_id: str | None = None
    referral_document_id: str | None = None
    referral_not_applicable: bool | None = None

    # The revision the client actually read (see `Claim.revision`). Optional —
    # a mismatch 409s, absence skips the check. Both UIs always send it.
    expected_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _is_a_real_change(self) -> _ClaimAmendBase:
        touched = self.model_fields_set - _AMEND_CONTROL_FIELDS
        if not touched:
            # Otherwise an empty body bumps the revision, supersedes the AI
            # review and posts the member a notice saying they changed
            # something — for a request that changed nothing.
            raise ValueError("name at least one field to change")
        cleared = sorted(f for f in touched & _NOT_CLEARABLE if getattr(self, f) is None)
        if cleared:
            raise ValueError(f"these fields cannot be cleared: {', '.join(cleared)}")
        return self


class ClaimAmendIn(_ClaimAmendBase):
    """A member correcting their own claim, until a broker decides it."""

    remarks: str | None = Field(default=None, max_length=500)


class ClaimBrokerAmendIn(_ClaimAmendBase):
    """An assessor correcting what the member stated.

    Deliberately NOT the same endpoint as `ClaimAssessmentIn`: that one records
    facts the BROKER owns and the member never stated (sector, admission dates,
    payroll treatment, settlement dates). This one rewrites the member's own
    account of the claim, which is a different act with a different trail — and,
    once the claim is settled, a different bar.
    """

    # Which SOB benefit item the claim draws on. Broker attribution, never the
    # member's: it decides the utilization bucket, and the member's form has no
    # benefit picker at all.
    benefit_key: str | None = Field(default=None, max_length=255)
    # REQUIRED once the claim is settled (see `claims.assert_amendment_reason`).
    # Not optional-with-a-default there: a blank reason on a corrected payment
    # figure is an audit row that explains nothing, which is the same as none.
    reason: str | None = Field(default=None, max_length=500)


class LogCaseCreateIn(BaseModel):
    """A LOG case entered by an assessor from an emailed request.

    Deliberately laxer than `ClaimCreateIn`: `provider_name`, `invoice_number`
    and `diagnosis` are optional, because an admission-guarantee email routinely
    carries none of them and a form that refuses to save without them means the
    request never gets recorded at all. Documents are attached afterwards
    through `POST /claims/{id}/documents`, and are optional too.
    """

    claim_kind: str = Field(default="insured", pattern="^(insured|flex)$")
    product_code: str | None = Field(default=None, max_length=64)
    flex_category_name: str | None = Field(default=None, max_length=255)
    dependant_id: str | None = None
    sub_type: str | None = Field(default=None, max_length=64)
    incurred_date: date
    provider_name: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=128)
    diagnosis: str | None = Field(default=None, max_length=512)
    remarks: str | None = Field(default=None, max_length=2000)
    amount_claimed: float = Field(gt=0, le=1_000_000)
    currency: str = Field(default="SGD", min_length=3, max_length=8)
    # Provenance of the request — all optional, stored on `intake_meta`.
    received_via: str | None = Field(
        default=None, pattern="^(email|phone|hr|hospital|other)$"
    )
    received_on: date | None = None
    requested_by: str | None = Field(default=None, max_length=255)


class ClaimCaseTypeIn(BaseModel):
    """Reclassify a case. The reason is mandatory: this is a correction to the
    record and the record should say why it was made."""

    case_type: Literal["claim", "log"]
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ClaimOut(_Base):
    id: str
    claim_kind: str
    # Claim category (models/claim.py CASE_TYPE_*). Members only ever see their
    # own submissions, so this is display context, never a gate on their side.
    case_type: str = "claim"
    origin: str = "portal"
    product_code: str | None = None
    benefit_key: str | None = None
    flex_category_name: str | None = None
    claim_type: str
    sub_type: str | None = None
    visit_type: str | None = None
    incurred_date: date
    provider_name: str | None = None
    invoice_number: str | None = None
    doctor_name: str | None = None
    diagnosis: str | None = None
    remarks: str | None = None
    referral_document_id: str | None = None
    referral_document: StoredDocumentOut | None = None
    # Mirrors the form declaration (from form_fields) for display.
    referral_not_applicable: bool = False
    amount_claimed: float
    currency: str
    amount_converted: float | None = None
    amount_approved: float | None = None
    status: str
    # The human-quotable reference, minted at submit. Member-visible on purpose:
    # it is the string they are asked for when they call about the claim, so
    # hiding it here would leave support with nothing to look a claim up by.
    reference_no: str | None = None
    # When the insurer paid. Member-visible for the same reason — "approved" and
    # "in my account" are different questions and they only ask the second.
    paid_on: date | None = None
    dependant_id: str | None = None
    # Resolved claimant display name when the claim is for a dependant.
    dependant_name: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decision_notes: str | None = None
    created_at: datetime
    documents: list[StoredDocumentOut] = Field(default_factory=list)
    # Required-document slots this claim must fill at submit (resolved from the
    # claim's own product/sub-type/hospital) — drives the tagged-upload UI on
    # the draft/needs_info detail page so resubmission can satisfy every slot.
    required_doc_slots: list[DocSlotOut] = Field(default_factory=list)

    # ── Amendment (docs/CLAIM_AMENDMENT_PLAN.md) ─────────────────────────────
    #
    # Whether the CLAIMANT may still change this claim, and the sentence to show
    # them when they may not. Both filled by `claims.member_editability` through
    # the shared `populate_claim_out`.
    #
    # **SERVED, never re-derived in TypeScript.** The portal used to answer this
    # itself (`status === "draft" || status === "needs_info"`), which is the
    # same drift class as mirroring `PENDING_STATUSES` into the client — and
    # exactly the mirror that stopped being true when the window widened to
    # "until the broker decides". The frontend renders the button off this flag
    # and the notice off this string; it must never switch on `status`.
    member_editable: bool = False
    member_edit_block: str | None = None
    # Whether the member may SEND it — narrower than `member_editable`, and
    # served separately because the two diverged the moment editing stayed open
    # after submission. A claim in review is theirs to CORRECT but not theirs to
    # send again, and one flag answering both questions put a Send button on
    # every claim in the queue that could only ever 409.
    member_can_submit: bool = False
    # Whether this claim must name the treating doctor (pre-/post-hospitalisation
    # only). SERVED, never inferred — an edit form that decides by looking at
    # whether `doctor_name` is already set gets a legacy claim exactly wrong: it
    # hides the control on the one claim that needs it.
    requires_doctor_name: bool = False
    # The optimistic-concurrency token (see `Claim.revision`). Member-visible
    # because the member's own amendment sends it back — two devices on one
    # claim is the ordinary case, not an exotic one.
    revision: int = 0
    amended_at: datetime | None = None


class ClaimList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ClaimOut] = Field(default_factory=list)


# ── AI review (broker-only — members never see fraud signals) ─────────────────


class ClaimAIReviewSummary(_Base):
    id: str
    status: str  # pending | complete | error
    verdict: str | None = None  # clean | flagged
    confidence: float | None = None
    summary: str | None = None
    created_at: datetime


class ClaimAIReviewOut(ClaimAIReviewSummary):
    extractions: list[dict[str, Any]] | None = None
    field_comparisons: list[dict[str, Any]] | None = None
    rule_results: list[dict[str, Any]] | None = None
    vision_checks: list[dict[str, Any]] | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_estimate_usd: float | None = None
    error_detail: str | None = None
    superseded: bool = False


# Broker view carries the claimant context the member view doesn't need.
class BrokerClaimOut(ClaimOut):
    client_id: str
    policy_year_id: str
    employee_id: str
    staff_id: str | None = None
    employee_name: str | None = None
    ai_review: ClaimAIReviewSummary | None = None
    # Provenance of a broker-entered case, flattened out of `intake_meta` (which
    # is untyped JSON and is read defensively — see services/log_cases.py).
    received_via: str | None = None
    received_on: date | None = None
    requested_by: str | None = None
    # Remaining amount in the claim's tightest utilization bucket (see
    # `remaining_for_claim`); None = no numeric limit known. Computed on the
    # single-claim detail only — the list stays cheap.
    remaining_limit: float | None = None
    # Member replies nobody here has opened. Counted for a whole page in ONE
    # grouped query — a member waiting on an answer is the reason to open the
    # claim, so it has to be visible in the queue rather than inside the sheet.
    unread_member_messages: int = 0

    # ── Settlement (see services/claim_settlement.py) ────────────────────────
    sent_to_insurer_at: datetime | None = None
    insurer_deadline_on: date | None = None
    # What the insurer actually paid, which may fall short of what we approved.
    payment_amount: float | None = None
    hospital_type: str | None = None
    # The sector DERIVED from the provider (`sg_hospitals.sector_from_provider`)
    # — served, never re-derived in TypeScript, because the claims report picks
    # its label from the same resolver. `hospital_type` above is the assessor's
    # OVERRIDE: null there means "use this", not "nobody has looked".
    hospital_type_derived: str | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    taxable: bool | None = None
    cpf_claimable: bool | None = None
    # BROKER-ONLY, and the reason this lives here rather than on `ClaimOut`:
    # `remarks` is the member's note and they can read it back, this one they
    # must not. Adding it to the shared base would publish every assessor's
    # working note to the portal.
    admin_remarks: str | None = None
    # Whether the claim draws on an inpatient benefit
    # (`claim_intake.is_inpatient_product`). SERVED, never mirrored in
    # TypeScript: it decides whether the assessment form offers hospital sector
    # and admission dates, and the claims report decides its columns from the
    # same helper. A product-code list in the frontend would drift, and the
    # failure is silent in the worst direction — the form hides the sector
    # field on a hospitalisation claim while the report keeps printing the
    # column, so it is blank on every row with no way to fill it.
    is_inpatient: bool = False
    # Derived, never stored — an unpaid claim's overdue count changes nightly
    # and there is no event to recompute a stored copy on.
    servicer_days: int | None = None
    insurer_days: int | None = None
    days_over_deadline: int | None = None


class ClaimSendToInsurerIn(BaseModel):
    """Dispatch an accepted claim. Both dates optional — omitted means
    "now" and "the default turnaround from now"."""

    sent_on: date | None = None
    deadline_on: date | None = None
    turnaround_days: int | None = Field(default=None, gt=0, le=365)
    note: str | None = Field(default=None, max_length=2000)


class ClaimPaymentIn(BaseModel):
    """Record the insurer's payment advice."""

    paid_on: date
    # Defaults to `amount_approved` when omitted. `ge=0` not `gt=0`: a zero
    # settlement is a real advice (fully offset against an excess) and refusing
    # it would leave the claim stuck in `sent_to_insurer` forever.
    amount: float | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=2000)


class ClaimAssessmentIn(BaseModel):
    """Assessor-entered detail that no document extraction supplies.

    Every field is optional and applied only when PRESENT (`model_fields_set`),
    so a form that edits one field cannot blank the rest.

    **The settlement dates are AMENDMENTS, not transitions.** `send-to-insurer`
    and `payment` move the claim's status and are offered only from the one
    status each is legal in — so a claim that reached `paid` without passing
    through dispatch (a LOG case settled outside the flow, a migrated row, a
    date keyed in wrongly) had no path back to correcting them: the button that
    sets them is gone, permanently. These fields correct the RECORD and leave
    the status alone, which is what an amendment is. Guarded server-side to
    claims that have actually reached the insurer leg — backfilling a dispatch
    date onto a draft would invent a history and start its SLA counters.
    """

    hospital_type: str | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    taxable: bool | None = None
    cpf_claimable: bool | None = None
    admin_remarks: str | None = Field(default=None, max_length=4000)

    # ── Settlement amendments ────────────────────────────────────────────────
    # A DATE, though the column is a timestamp: the broker is transcribing an
    # insurer's advice, not stamping the moment they keyed it in — the same
    # reason `send_to_insurer` accepts `sent_on`.
    sent_to_insurer_on: date | None = None
    insurer_deadline_on: date | None = None
    paid_on: date | None = None
    payment_amount: float | None = Field(default=None, ge=0)


class BrokerClaimList(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[BrokerClaimOut] = Field(default_factory=list)


# ── Claim messages (the member <-> broker thread) ─────────────────────────────


class ClaimMessageOut(BaseModel):
    """One message, rendered for whichever surface asked.

    `mine` and `unread` are RELATIVE to the reader — the same row is `mine` on
    the broker surface and not on the member's. Both are filled by the two
    builders in `services/claim_messages.py`, never by `model_validate`: reading
    the model directly would hand the member the broker's `author_name`.
    """

    id: str
    # Exactly one of these is set — a message belongs to a claim's thread or to
    # a question's, never both (`services/claim_messages._post`).
    claim_id: str | None = None
    enquiry_id: str | None = None
    author_type: str  # system | broker | member
    author_name: str | None = None
    subject: str
    body: str
    # Set only on automatic notices (models.claim_message.EVENT_*).
    event: str | None = None
    created_at: datetime
    mine: bool = False
    unread: bool = False
    # NOTE: no `claim_type` / `claim_status` here. They existed to give a row of
    # the old flat cross-claim inbox the context a thread doesn't need — and
    # that inbox is gone, replaced by conversations, where the context is the
    # thread's SUBJECT and belongs to the conversation rather than to each
    # message inside it.


# ── Conversations ─────────────────────────────────────────────────────────────


class ConversationSubjectOut(BaseModel):
    """What a thread is ABOUT — the fields that let one conversation be told
    from another.

    Claim titles are composed CLIENT-side by the portal's existing `claimTitle`,
    which reads only the four naming fields below: the portal names a claim in
    one place and this list must not become a second. What the list adds under
    the title is the DATE and the AMOUNT, because a claim type is not unique on
    a real roster — one CDL member holds two "Emergency Accidental Outpatient
    Treatment" conversations and two "Follow up Pre-/Post-Hospitalisation" ones,
    and in the flat inbox this replaces the only thing separating them was a
    date inside a body snippet clamped to one line.
    """

    kind: str  # claim | enquiry
    id: str
    # ── claim ────────────────────────────────────────────────────────────────
    claim_kind: str | None = None
    claim_type: str | None = None
    sub_type: str | None = None
    product_code: str | None = None
    flex_category_name: str | None = None
    incurred_date: date | None = None
    amount_claimed: float | None = None
    currency: str | None = None
    # A claim's own status, or a question's `open | answered | closed`.
    status: str | None = None
    # ── question ─────────────────────────────────────────────────────────────
    # The member's own headline, which is a question's title.
    subject: str | None = None
    topic: str | None = None
    # The topic's member-facing name, SERVED rather than title-cased by each
    # client. The vocabulary has one home (`models.member_enquiry`), and the two
    # surfaces that print it were both getting it wrong from opposite ends: the
    # broker's queue rendered the raw key (`Question · clinics · answered`) and
    # the member's row dropped it entirely, so every question they had asked
    # read `Question` and nothing told two of them apart.
    topic_label: str | None = None
    # BROKER-side triage. A Letter of Guarantee request is the one topic where
    # the delay is the harm — the member is usually at an admissions counter —
    # so it sorts to the top of the queue and is marked there. Served on the
    # shared subject because both surfaces read one serializer; the member's own
    # screen deliberately does not render it (see models/member_enquiry.py).
    topic_urgent: bool = False
    # A question may NAME a claim without belonging to one. It is the same
    # shape nested, so the client composes its label with the same helper —
    # a reference, never a second thread on that claim. See the design doc.
    about_claim: ConversationSubjectOut | None = None


class EnquiryTopicOut(BaseModel):
    """One row of the "What's it about?" picker.

    `routes_to_claim` is the option that does NOT create a question: it sends
    the member to the claim's own thread. A claim question answered anywhere
    else would be a second conversation about one claim, each readable while
    the other still shows unread. Served rather than hardcoded in TS so the
    vocabulary — and which option routes — has one home.
    """

    key: str
    label: str
    routes_to_claim: bool = False


class EnquiryCreateIn(BaseModel):
    topic: str = Field(min_length=1, max_length=32)
    subject: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=2000)
    # Optional CONTEXT — offered only on non-claim topics. Validated through
    # `load_member_claim`, so a claim the member does not own 404s exactly as it
    # does everywhere else and this cannot be used to probe.
    about_claim_id: str | None = None

    # A SUBJECT is one line by definition, so its whitespace is collapsed.
    @field_validator("subject")
    @classmethod
    def _one_line(cls, v: str) -> str:
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    # A BODY keeps the author's line breaks. Both surfaces render it
    # `whitespace-pre-line`, and the collapse used to be applied here too —
    # under a length branch, so a 200-character two-paragraph question arrived
    # flattened while a 300-character one did not, and the member's own later
    # replies (validated by `MemberMessageIn`, which only strips) kept theirs.
    # One member, one thread, two different treatments of the same key.
    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class EnquiryStatusIn(BaseModel):
    action: str = Field(pattern="^(close|reopen)$")


class EnquiryOut(BaseModel):
    """A question's header — the thread page, and the broker's sheet."""

    id: str
    topic: str
    # Served for the same reason `ConversationSubjectOut.topic_label` is — the
    # vocabulary has one home, and neither surface should be title-casing a key.
    topic_label: str | None = None
    topic_urgent: bool = False
    subject: str
    status: str
    about_claim: ConversationSubjectOut | None = None
    created_at: datetime
    # Broker surfaces only.
    employee: ConversationEmployeeOut | None = None


class ConversationEmployeeOut(BaseModel):
    """Whose thread it is. BROKER surfaces only — the member's own list has no
    business naming them to themselves, and this is the whole point of the
    broker's queue: knowing WHO is waiting without opening anything."""

    id: str
    staff_id: str
    employee_name: str | None = None


class ConversationOut(BaseModel):
    """One thread, as a work item.

    There is deliberately no `awaiting` field: who a thread is waiting on IS
    `last_message.author_type`, and a second spelling of one fact is a second
    thing that has to be kept true. Nor is there a "waiting since" — that is
    `last_message.created_at`, read by whoever wants to print it.
    """

    subject: ConversationSubjectOut
    last_message: ClaimMessageOut
    message_count: int
    unread: int
    # Broker surfaces only; absent on the member's own conversations.
    employee: ConversationEmployeeOut | None = None


class ConversationList(BaseModel):
    total: int
    offset: int
    limit: int
    # Unread MESSAGES across every conversation, not just this page — it is what
    # the shell badge and the home tile state. Named apart from
    # `ConversationOut.unread` (which counts one thread) so a payload carrying
    # both cannot be misread.
    unread_total: int = 0
    items: list[ConversationOut] = Field(default_factory=list)


class MemberMessageIn(BaseModel):
    """A member's reply. No subject — the inbox supplies one, and a second box
    on a phone keyboard buys nothing."""

    body: str = Field(min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class BrokerMessageIn(BaseModel):
    # Optional: falls back to `claim_messages.DEFAULT_BROKER_SUBJECT`, which is
    # what the member's inbox lists when the broker just types a body.
    subject: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class MessagesReadOut(BaseModel):
    marked: int


class ClaimDecisionIn(BaseModel):
    action: str = Field(pattern="^(approve|reject|needs_info)$")
    note: str | None = Field(default=None, max_length=2000)
    approved_amount: float | None = Field(default=None, gt=0)
    # Approving beyond the bucket's remaining limit 409s (`limit_exceeded`)
    # unless the broker explicitly acknowledges the overrun.
    acknowledge: bool = False
    # The `Claim.revision` the assessor actually read. A member may amend their
    # claim right up to this decision, so without it a broker can read $150,
    # have it corrected to $105 under them, and approve a figure that is no
    # longer on the claim. A mismatch 409s (`claim_amended`).
    #
    # Optional, and that is not laziness: a LOG case created and decided in one
    # assessor flow has no stale read to guard against, and making it mandatory
    # would break every existing caller. The queue always sends it.
    expected_revision: int | None = Field(default=None, ge=0)


# ── Utilization (computed-on-read; see services/utilization.py) ───────────────


class UtilizationBucket(BaseModel):
    product_code: str | None = None
    product_name: str | None = None
    benefit_key: str | None = None  # None = product-level roll-up
    limit: float | None = None  # parsed numeric annual limit, when known
    limit_display: str | None = None  # verbatim limit text for the UI
    approved: float = 0.0
    pending: float = 0.0  # in-flight claims — shown separately, never subtracted
    remaining: float | None = None  # limit - approved
    claim_count: int = 0
    # The claims `pending` was summed FROM — same contract as
    # `FlexUtilization.pending_claim_ids`, and served for the same reason:
    # "which statuses count as pending" is `utilization.PENDING_STATUSES`,
    # defined BY SUBTRACTION from the settled set, so it grows a new member the
    # day a status is added. A client filtering the claim list itself would be
    # reimplementing that rule and would start offering a different set from the
    # number it sits under.
    pending_claim_ids: list[str] = Field(default_factory=list)
    # Claims against coverage no longer on the statement (coverage changed
    # after submission).
    orphaned: bool = False
    # A limit text exists (with digits) but no annual limit could be derived
    # (per-unit/percent value or parse failure) — the over-limit approve guard
    # is INACTIVE for this bucket, which is not the same as "unlimited".
    limit_unparsed: bool = False


class FlexCategoryUtilization(BaseModel):
    name: str
    sub_limit: float | None = None
    approved: float = 0.0
    pending: float = 0.0
    remaining: float | None = None


class FlexProration(BaseModel):
    """How the annual allowance was scaled to the member's own cover period.

    Mirrors ``api.FlexProrationLine``; carried here too because the member's
    wallet page is where someone whose allowance dropped will look for the
    reason, and a reduced figure with nothing explaining it is the one members
    dispute. ``note`` is the printable fraction ("6/12 months"), SERVED so the
    client never recomputes a month count that could drift from the figure
    beside it.
    """

    basis: str
    factor: float
    served: int
    total: int
    full_amount: float
    note: str


class FlexUtilization(BaseModel):
    currency: str | None = None
    wallet_amount: float | None = None
    # Present only when the allowance was pro-rated.
    proration: FlexProration | None = None
    price_tags_total: float | None = None
    flex_balance: float | None = None  # wallet - enrollment price-tags
    approved: float = 0.0  # approved flex claims
    pending: float = 0.0
    # The claims `pending` was summed FROM, so a surface that prints the figure
    # can open what is behind it. Served rather than re-derived in the client:
    # "which statuses count as pending" is `utilization.PENDING_STATUSES`, which
    # is defined BY SUBTRACTION and therefore grows a new member the day a
    # status is added — a mirrored copy would silently start offering a
    # different set from the number it sits under.
    pending_claim_ids: list[str] = Field(default_factory=list)
    available: float | None = None  # flex_balance - approved
    categories: list[FlexCategoryUtilization] = Field(default_factory=list)


class UtilizationOut(BaseModel):
    policy_year_id: str
    insured: list[UtilizationBucket] = Field(default_factory=list)
    flex: FlexUtilization | None = None


# ── Coverage options (drives the member claim-form picker) ────────────────────


class HospitalOut(BaseModel):
    name: str
    sector: str  # govt | private


# One entry in the claim-type dropdown. The sub-type is folded into the
# selection (inpatient setting / GP rider), never a second picker.
class ClaimTypeOption(BaseModel):
    label: str
    sub_type: str | None = None
    # Whether this entry must name the treating doctor (pre-/post-hospitalisation
    # only). SERVED, never mirrored: the frontend would otherwise have to match
    # on the sub-type LABEL, and a relabel there is silent — the field would
    # simply stop being asked for while the server kept requiring it.
    requires_doctor_name: bool = False
    # Required-document slots for this claim type. When the requirement
    # depends on the hospital (Hospitalisation/Day Surgery), `doc_slots` is
    # the unlisted-hospital default and `doc_slots_by_sector` carries the
    # govt/private sets keyed by `HospitalOut.sector`.
    doc_slots: list[DocSlotOut] = Field(default_factory=list)
    doc_slots_by_sector: dict[str, list[DocSlotOut]] | None = None


class InsuredClaimOption(BaseModel):
    product_code: str
    product_name: str | None = None
    plan_code: str | None = None
    annual_policy_limit: str | None = None
    covers_dependants: bool = False
    covered_dependant_ids: list[str] = Field(default_factory=list)
    # Insurer + the member's ID with that insurer (from the roster
    # `insurer_member_ids` map). Display-only: the ID shown on the claim form
    # keys off the selected claim type's product/insurer. Blank when the roster
    # carries no ID for this insurer.
    insurer: str | None = None
    insurer_member_id: str | None = None
    # Claim-intake profile (claim_intake.py) — drives the conditional form
    # fields: SP referral requirement, diagnosis search.
    sub_types: list[str] = Field(default_factory=list)
    requires_referral: bool = False
    diagnosis_group: str | None = None
    diagnosis_required: bool = False
    # Outpatient / Inpatient / other grouping + the dropdown entries this
    # product contributes (plan-aware: GP riders appear only when the member's
    # schedule carries a matching row).
    category: str = "other"
    claim_types: list[ClaimTypeOption] = Field(default_factory=list)


class FlexClaimCategoryOption(BaseModel):
    name: str
    sub_limit: float | None = None
    note: str | None = None


class FlexClaimOptions(BaseModel):
    currency: str | None = None
    wallet_amount: float | None = None
    flex_balance: float | None = None
    categories: list[FlexClaimCategoryOption] = Field(default_factory=list)
    # Required-document slots for flex claims (the generic invoice/receipt).
    doc_slots: list[DocSlotOut] = Field(default_factory=list)
    # The flex scheme's effective window, clamped to the member's own cover.
    # Genuinely different from the policy year (a scheme can start mid-year), so
    # the form must not reuse the year's span for a flex claim.
    claimable_from: str | None = None
    claimable_to: str | None = None


class CoverageOptionsOut(BaseModel):
    policy_year_start: str
    policy_year_end: str
    # The dates an INSURED claim may be incurred on, resolved by the same
    # function submit enforces (`claims.claim_period_window`). SERVED so the
    # form can state the bound up front instead of surfacing a 422 after the
    # member has filled the whole thing in — and so a leaver, whose window ends
    # on their last day, is told that rather than discovering it.
    #
    # **NULL when the window refuses every date** (`ClaimWindow.is_empty` — a
    # leaver whose cover ended before the period began). An inverted range
    # served verbatim reaches the form as `min > max` on the date input and
    # tells the member to "pick a date between 15 Jul 2026 and 1 Jun 2026" for
    # every date they try; `claim_block` carries the real reason instead.
    claimable_from: str | None = None
    claimable_to: str | None = None
    insured: list[InsuredClaimOption] = Field(default_factory=list)
    flex: FlexClaimOptions | None = None
    # Why a claim kind was withheld from this response — the member's cover
    # ended before the period began, so its options are empty and its window is
    # null. `None` in the ordinary case. Worded once, in
    # `ClaimWindow.empty_note`, which is also the 422 submit raises.
    claim_block: str | None = None
    dependants: list[dict[str, Any]] = Field(default_factory=list)  # {id, name, relationship}
    # Single source of truth for the currency picker (claim_intake.py).
    currencies: list[str] = Field(default_factory=list)
    # Hospital picker for inpatient claims (sg_hospitals.py) — sector drives
    # the document requirements.
    hospitals: list[HospitalOut] = Field(default_factory=list)


# ── Claim intake autofill (document-driven prefill) ───────────────────────────


class IntakeClaimant(BaseModel):
    kind: str  # "self" | "dependant"
    dependant_id: str | None = None
    name: str | None = None
    confidence: float = 0.0


class IntakeFields(BaseModel):
    provider_name: str | None = None
    incurred_date: str | None = None  # ISO date
    invoice_number: str | None = None
    amount: float | None = None
    currency: str | None = None
    diagnosis: str | None = None
    # The treating doctor read off the bill. Always suggested when the document
    # names one; the form only asks for it on pre-/post-hospitalisation claims.
    doctor_name: str | None = None


class IntakeDocument(BaseModel):
    """One uploaded document in an autofill set, with its recognised type and
    the required-document slot it fills (when unambiguous)."""

    file_name: str
    # 0-based position in the ORIGINAL upload — the form joins its File objects
    # to these documents on this (robust to duplicate file names, and to the
    # endpoint skipping an unreadable file mid-set).
    upload_index: int = 0
    detected_doc_type: str | None = None
    doc_slot: str | None = None
    # Multi-claim uploads: when the set carries several DISTINCT invoices (one
    # visit each), every invoice document anchors its own claim — this is its
    # 0-based order (0 = the claim prefilled now). None = supporting document.
    claim_index: int | None = None
    # A LATER anchor's OWN reading, used to prefill its claim when the form
    # advances to it. None for the first claim + supporting docs (they prefill
    # from the top-level merged suggestion, never from here).
    fields: IntakeFields | None = None
    # Field names of `fields` the AI was unsure about (only set alongside
    # `fields`) — the form flags them when it advances to this claim.
    low_confidence: list[str] = Field(default_factory=list)


class ClaimIntakeSuggestionOut(BaseModel):
    """What the AI read off an uploaded receipt, mapped to claim-form fields.
    Every value is a SUGGESTION — the member confirms/edits before submit."""
    # False when extraction is unavailable (no AI provider / budget / breaker /
    # parse fault) — the form stays fully manual.
    available: bool = True
    reason: str | None = None
    document_type: str | None = None
    # Broker-recognised document type (claim_doc_types registry display name,
    # e.g. "Discharge Summary", "Tax Invoice (Finalised)") when identified —
    # mirrors the primary document (first that fills a slot).
    detected_doc_type: str | None = None
    # Required-document slot key the primary upload fills, when unambiguous.
    doc_slot: str | None = None
    # Per-document classification for the whole uploaded set (up to 3) — the
    # form drops each file into the slot it fills.
    documents: list[IntakeDocument] = Field(default_factory=list)
    # True when the set carries ≥2 DISTINCT invoices — separate visits that
    # need one claim each. The top-level fields prefill the FIRST invoice's
    # claim; the rest ride on `documents[].claim_index`/`fields`.
    multi_claim: bool = False
    # Preselected claimant (self / a dependant), when a patient name matched.
    claimant: IntakeClaimant | None = None
    # Encoded claim-type selection (`insured:<code>:<idx>` / `flex:<name>`) when
    # the setting maps unambiguously to ONE of the member's claim types.
    claim_selection: str | None = None
    # Plausible claim-type selections when the setting is ambiguous — the member
    # picks. Empty when we have no signal at all.
    claim_candidates: list[str] = Field(default_factory=list)
    fields: IntakeFields = Field(default_factory=IntakeFields)
    # Field names (of `fields`) the model was unsure about — the UI flags them.
    low_confidence: list[str] = Field(default_factory=list)


# ── Claim document types (broker-configurable registry) ───────────────────────


class ClaimDocKeyField(BaseModel):
    """One completeness-check field. ``keywords`` are the label-match tokens;
    empty → the field matches on its own name. ``optional`` fields are checked
    but their absence is never a completeness warning (e.g. Surgery on a
    non-surgical discharge summary)."""

    name: str = Field(min_length=1, max_length=64)
    keywords: list[str] = Field(default_factory=list, max_length=16)
    optional: bool = False


class ClaimDocTypeIn(BaseModel):
    display: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=32)
    key_fields: list[ClaimDocKeyField] = Field(default_factory=list, max_length=32)
    sector: str | None = Field(default=None, pattern="^(govt|private)$")
    slot_key: str | None = Field(default=None, max_length=64)


class ClaimDocTypeOut(BaseModel):
    id: str
    key: str
    display: str
    aliases: list[str] = Field(default_factory=list)
    key_fields: list[ClaimDocKeyField] = Field(default_factory=list)
    sector: str | None = None
    slot_key: str | None = None
    # True for a row seeded from the in-code defaults (key match) — the UI
    # labels these; they're still fully editable.
    is_default: bool = False


class DiagnosisOut(BaseModel):
    label: str
    icd10: str | None = None


# ── Claim review rule setup (per claim type, broker-configurable) ─────────────


class ReviewFieldMapModel(BaseModel):
    """One claim-form ↔ document field pair the AI review compares."""

    portal_field: str = Field(min_length=1, max_length=64)
    document_field: str = Field(min_length=1, max_length=128)
    mode: Literal["fuzzy", "exact", "numeric"] = "fuzzy"
    tolerance: float | None = Field(default=None, ge=0)
    # Spend an extra AI vision pass on this field when the text comparison
    # disagrees. Purely a cost/accuracy control.
    verify_with_vision: bool = False
    # Flag the claim when it states this field but NO document substantiates
    # it. Independent of the vision flag on purpose — turning off a vision
    # re-check must never switch off the unsubstantiated-value guard.
    require_evidence: bool = False


class ReviewAIRuleModel(BaseModel):
    """One AI-judged business rule. Only a CRITICAL failure can flag the
    claim; warning/info failures surface to the broker without auto-flagging."""

    id: str | None = Field(default=None, max_length=64)
    rule: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="general", min_length=1, max_length=64)
    severity: Literal["critical", "warning", "info"] = "critical"


class ClaimReviewConfigIn(BaseModel):
    claim_kind: Literal["insured", "flex"]
    # Product code (insured) or flex benefit-category name (flex).
    claim_key: str = Field(min_length=1, max_length=128)
    display_label: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    # The caps bound review-prompt growth.
    field_maps: list[ReviewFieldMapModel] = Field(min_length=1, max_length=30)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list, max_length=60)
    # Extra document families required ON TOP of the automatic derivation.
    required_documents: list[str] = Field(default_factory=list, max_length=15)

    @field_validator("claim_key", "display_label")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length alone accepts "   ", which the API then normalizes to ""
        # — a row that would be committed and thereafter fail to serialize.
        # Normalize here so what validates is what gets stored.
        cleaned = " ".join(v.split())
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ClaimReviewConfigOut(BaseModel):
    """Deliberately NOT a subclass of ``ClaimReviewConfigIn``.

    Reading must never fail: a legacy or hand-edited row that violates a
    write-side constraint has to stay listable (and therefore deletable),
    not 500 the whole company's config surface. The service's defensive
    reader (`claim_review_configs.config_from_row`) already drops malformed
    entries and bounds sizes, so the values here are sane by construction.
    """

    id: str
    claim_kind: str
    claim_key: str
    # Server-computed join key — see `claim_review_configs.type_key`. The UI
    # matches configs to claim types on this, never on a re-derived key.
    key: str
    display_label: str
    enabled: bool = True
    field_maps: list[ReviewFieldMapModel] = Field(default_factory=list)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)


class ReviewDefaultConfigOut(BaseModel):
    """The in-code default setup — prefills the editor when a claim type is
    first customized."""

    field_maps: list[ReviewFieldMapModel] = Field(default_factory=list)
    ai_rules: list[ReviewAIRuleModel] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)


class ReviewClaimTypeOut(BaseModel):
    """One claim type of the company (the config scope vocabulary)."""

    claim_kind: Literal["insured", "flex"]
    claim_key: str
    # Server-computed join key — see `claim_review_configs.type_key`.
    key: str
    display_label: str
    sub_types: list[str] = Field(default_factory=list)


class ReviewScopeOptionsOut(BaseModel):
    claim_types: list[ReviewClaimTypeOut] = Field(default_factory=list)
    default_config: ReviewDefaultConfigOut
    # False when NO benefit year is flagged current. The vocabulary is read from
    # that year alone, so this is the difference between "this company has
    # nothing claimable configured" and "the year holding the products was never
    # made current" — which look identical from an empty list, and only the
    # second is a one-click fix (the whole member portal is dark meanwhile).
    has_current_year: bool = False


class ReviewPromptPreviewOut(BaseModel):
    prompt: str


class SourceReviewConfigOut(BaseModel):
    """A configured claim type of ANOTHER company, offered for import."""

    id: str
    claim_kind: str
    claim_key: str
    # Server-computed join key — see `claim_review_configs.type_key`. Lets the
    # import dialog mark "already customized here" against the active company's
    # configs without re-deriving the key.
    key: str
    display_label: str
    enabled: bool
    field_map_count: int
    rule_count: int
    required_document_count: int


class ImportSourceCompanyOut(BaseModel):
    """A company whose rule setup may be imported from (same broker firm)."""

    id: str
    name: str
    configured_count: int


class ImportReviewConfigsIn(BaseModel):
    source_client_id: str = Field(min_length=1, max_length=36)
    config_ids: list[str] = Field(min_length=1, max_length=50)


class ImportReviewConfigsOut(BaseModel):
    imported: list[ClaimReviewConfigOut] = Field(default_factory=list)


class DiagnosisSearchOut(BaseModel):
    group: str | None = None
    items: list[DiagnosisOut] = Field(default_factory=list)


# ── Dependant self-add (portal) ───────────────────────────────────────────────


class PortalDependantCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    relationship: str = Field(min_length=1, max_length=64)
    dob: date | None = None
    gender: str | None = Field(default=None, max_length=16)
    id_no: str | None = Field(default=None, max_length=64)


class DependantApprovalIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=2000)
