"""No-login QR enrolment — public form, intake review and HR download batches."""
from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.schemas.enrollment import ProductTierSetOut
from app.schemas.enrollment_forms import (
    FormClause,
    FormRule,
    PlanFactOut,
    ProductContributionOut,
)

PublicFormStatus = Literal["open", "not_yet_open", "closed", "paused"]
IntakeStatus = Literal["pending", "linked", "rejected"]
RejectReason = Literal["duplicate", "not_employee", "wrong_company", "withdrawn", "other"]
DependantAction = Literal["add", "withdraw", "update"]
ProductAction = Literal["no_change", "enrol", "withdraw"]

_KEY = r"^[a-z0-9-]{1,16}$"


def _printable(value: object) -> object:
    """Refuse control characters in anything a visitor types: PostgreSQL
    rejects NUL in text, and none of these fields has a use for them."""
    if isinstance(value, str) and any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError("Remove unsupported characters.")
    return value


DependantKey = Annotated[str, StringConstraints(pattern=_KEY)]
ClauseId = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z0-9_-]+$")]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


# ── Public form (employee, no login) ─────────────────────────────────────────


class PublicCohortOut(BaseModel):
    """One employee cohort of a product, as the paper form listed it."""

    category_id: str
    label: str
    # Shared by the same cohort across products, so choosing a cohort for one
    # benefit can preselect it for the next.
    cohort: str
    tier_set: ProductTierSetOut


class PublicProductOut(BaseModel):
    product_code: str
    product_name: str | None
    insurer: str | None = None
    cohorts: list[PublicCohortOut]
    # Dependant eligibility windows (age next birthday) for this product.
    age_limits: dict[str, dict[str, int]] = Field(default_factory=dict)
    gst_included: bool = False


class PublicDocumentOut(BaseModel):
    id: str
    label: str
    url: str | None = None
    has_file: bool = False


class PublicFormOut(BaseModel):
    status: PublicFormStatus
    company_name: str
    window_name: str
    closes_at: datetime
    opens_at: datetime
    # Present only while the form accepts submissions.
    title: str | None = None
    window_type: str | None = None
    policy_start: date | None = None
    policy_end: date | None = None
    intro_lines: list[str] = Field(default_factory=list)
    helpline: str | None = None
    submission_note: str | None = None
    eligibility_notes: list[str] = Field(default_factory=list)
    clauses: list[FormClause] = Field(default_factory=list)
    documents: list[PublicDocumentOut] = Field(default_factory=list)
    rules: list[FormRule] = Field(default_factory=list)
    products: list[PublicProductOut] = Field(default_factory=list)
    automatic: list[str] = Field(default_factory=list)
    contributions: list[ProductContributionOut] = Field(default_factory=list)
    plans: list[PlanFactOut] = Field(default_factory=list)
    allow_dependant_changes: bool = True
    max_children: int = 6


class PublicParticularsIn(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _check_printable(cls, value: object) -> object:
        return _printable(value)

    name: str = Field(min_length=2, max_length=120)
    id_no: str = Field(min_length=5, max_length=20)
    staff_id: str = Field(min_length=1, max_length=64)
    gender: Literal["M", "F"]
    dob: date | None = None
    contact_no: str = Field(min_length=6, max_length=20, pattern=r"^[0-9+()\- ]{6,20}$")
    email: str = Field(min_length=5, max_length=160, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    job_grade: str | None = Field(default=None, max_length=60)
    date_of_hire: date | None = None
    marital_status: Literal["single", "married", "divorced", "widowed"] | None = None

    @field_validator("name", "staff_id", "job_grade", mode="before")
    @classmethod
    def _squash(cls, value: object) -> object:
        return _clean(value) if isinstance(value, str) else value

    @field_validator("id_no", mode="before")
    @classmethod
    def _id(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class PublicDependantIn(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def _check_printable(cls, value: object) -> object:
        return _printable(value)

    key: str = Field(pattern=_KEY)
    action: DependantAction
    relationship: Literal["spouse", "child"]
    name: str = Field(min_length=2, max_length=120)
    gender: Literal["M", "F"]
    id_no: str = Field(min_length=5, max_length=20)
    dob: date
    occupation: str | None = Field(default=None, max_length=80)
    nationality: str | None = Field(default=None, max_length=60)
    residence: str | None = Field(default=None, max_length=60)
    # A child is eligible only while unmarried, not working full time and not
    # serving National Service — the paper forms' definitions.
    child_confirmed: bool = False

    @field_validator("name", "occupation", "nationality", "residence", mode="before")
    @classmethod
    def _squash(cls, value: object) -> object:
        return _clean(value) if isinstance(value, str) else value

    @field_validator("id_no", mode="before")
    @classmethod
    def _id(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class PublicProductChoiceIn(BaseModel):
    product_code: str = Field(min_length=1, max_length=32)
    category_id: str = Field(min_length=1, max_length=36)
    action: ProductAction
    tier_key: str | None = Field(default=None, max_length=120)
    # Withdraw: end the employee's own voluntary cover too.
    cover_self: bool = False
    # Keys of the dependants (from ``dependants``) this choice covers or withdraws.
    covered: list[DependantKey] = Field(default_factory=list, max_length=12)


class PublicLifeEventIn(BaseModel):
    kind: Literal["new_hire", "marriage", "birth", "other"]
    date: date


class PublicSubmissionIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    particulars: PublicParticularsIn
    dependants: list[PublicDependantIn] = Field(default_factory=list, max_length=12)
    products: list[PublicProductChoiceIn] = Field(default_factory=list, max_length=20)
    life_event: PublicLifeEventIn | None = None
    accepted_clause_ids: list[ClauseId] = Field(default_factory=list, max_length=40)
    signature_name: str = Field(min_length=2, max_length=120)
    # Hidden honeypot: a person never fills it.
    website: str = Field(default="", max_length=200)

    @field_validator("signature_name", "website", mode="before")
    @classmethod
    def _printable_text(cls, value: object) -> object:
        return _printable(value)

    @model_validator(mode="after")
    def _unique_keys(self) -> PublicSubmissionIn:
        keys = [d.key for d in self.dependants]
        if len(keys) != len(set(keys)):
            raise ValueError("Each family member needs a unique key.")
        codes = [p.product_code for p in self.products]
        if len(codes) != len(set(codes)):
            raise ValueError("Each benefit can be chosen once.")
        return self


class PublicReceiptOut(BaseModel):
    reference_no: str
    submitted_at: datetime
    copy_key: str
    copy_expires_at: datetime


class PublicCopyIn(BaseModel):
    reference_no: str = Field(min_length=4, max_length=32)
    copy_key: str = Field(min_length=20, max_length=128)


# ── Broker: shared QR entry ──────────────────────────────────────────────────


class QrEntryOut(BaseModel):
    exists: bool
    enabled: bool = False
    window_id: str
    window_name: str
    window_status: str
    closes_at: datetime
    created_at: datetime | None = None
    rotated_at: datetime | None = None
    pending: int = 0
    linked: int = 0
    rejected: int = 0


class QrEntryLinkOut(BaseModel):
    url: str


class QrEntryUpdateIn(BaseModel):
    enabled: bool


# ── Broker: intake review ────────────────────────────────────────────────────


class IntakeItemOut(BaseModel):
    id: str
    reference_no: str
    status: IntakeStatus
    window_id: str | None
    window_name: str | None
    submitted_at: datetime
    claimed_staff_id: str | None
    claimed_name: str | None
    id_masked: str | None
    category_label: str | None
    requests: list[str]
    dependants: int
    linked_employee_id: str | None = None
    linked_employee_name: str | None = None
    linked_submission_id: str | None = None
    rejected_reason: str | None = None


class IntakeListOut(BaseModel):
    items: list[IntakeItemOut]
    total: int
    offset: int
    limit: int
    counts: dict[str, int]


class IntakeCandidateOut(BaseModel):
    employee_id: str
    staff_id: str
    employee_name: str | None
    id_masked: str | None
    category: str | None
    status: str
    match: list[str]
    existing_form: str | None = None


class IntakeDetailOut(BaseModel):
    item: IntakeItemOut
    particulars: dict[str, object]
    selections: list[dict[str, object]]
    family: list[dict[str, object]]
    life_event: dict[str, object] | None = None
    candidates: list[IntakeCandidateOut]


class IntakeLinkIn(BaseModel):
    employee_id: str = Field(min_length=1, max_length=36)
    note: str | None = Field(default=None, max_length=2000)
    # The employee already has a current form this period; filing this one
    # supersedes it, so the broker must say so explicitly.
    confirm_supersede: bool = False


class IntakeRejectIn(BaseModel):
    reason: RejectReason
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _other_needs_note(self) -> IntakeRejectIn:
        if self.reason == "other" and not (self.note or "").strip():
            raise ValueError("Add a short note when the reason is Other.")
        return self


# ── Broker: HR download batches ──────────────────────────────────────────────


FormBatchStatus = Literal["submitted", "acknowledged"]
_BATCH_STATUSES: tuple[FormBatchStatus, ...] = ("submitted", "acknowledged")


class BatchFilterIn(BaseModel):
    window_id: str | None = Field(default=None, max_length=36)
    statuses: list[FormBatchStatus] = Field(
        default_factory=lambda: list(_BATCH_STATUSES), min_length=1
    )
    include_pending_qr: bool = False
    exclude_ids: list[str] = Field(default_factory=list, max_length=1000)


class BatchPreviewItemOut(BaseModel):
    id: str
    kind: Literal["form", "intake"]
    reference_no: str
    version: int
    staff_id: str | None
    employee_name: str | None
    source: str
    status: str
    submitted_at: datetime | None


class BatchPreviewOut(BaseModel):
    items: list[BatchPreviewItemOut]
    total: int
    limit: int


class GrantOut(BaseModel):
    id: str
    state: Literal["ready", "active", "expired", "locked", "revoked"]
    recipient_note: str | None
    issued_at: datetime
    unused_expires_at: datetime
    activated_at: datetime | None
    expires_at: datetime | None
    downloads: int
    revoke_reason: str | None


class BatchOut(BaseModel):
    id: str
    label: str
    status: Literal["ready", "closed", "expired"]
    window_id: str | None
    file_count: int
    size_bytes: int
    created_at: datetime
    expires_at: datetime
    grant: GrantOut | None = None


class BatchListOut(BaseModel):
    items: list[BatchOut]


class GrantIssueIn(BaseModel):
    recipient_note: str | None = Field(default=None, max_length=255)


class GrantIssuedOut(BaseModel):
    grant: GrantOut
    url: str
    pin: str


# ── Public HR download ───────────────────────────────────────────────────────


HrDownloadState = Literal["ready", "verified", "active", "expired", "locked", "revoked", "in_use"]


class HrDownloadOut(BaseModel):
    state: HrDownloadState
    company_name: str
    label: str
    file_count: int
    size_bytes: int
    server_time: datetime
    expires_at: datetime | None = None
    access_seconds: int
    attempts_left: int | None = None
    downloads_left: int | None = None


class HrPinIn(BaseModel):
    pin: str = Field(pattern=r"^\d{6}$")
