"""Member claim endpoints — every access is scoped to the caller's own
Employee row via `resolve_member_employee`; a claim id belonging to anyone
else 404s (same not-403 convention as tenant scoping)."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from anthropic import RateLimitError
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_member_audit
from app.core.clock import today as business_today
from app.core.downloads import attachment_header
from app.core.pagination import MAX_LIMIT
from app.core.portal_auth import (
    CurrentMember,
    active_policy_year,
    assert_member_capability,
    get_current_member,
    resolve_member_employee,
)
from app.core.rate_limit import limiter
from app.core.storage import DOCUMENT_SUFFIXES, MAX_DOCUMENT_BYTES, get_storage
from app.core.uploads import saved_upload
from app.db.session import get_db
from app.models import Claim, Employee, PolicyYear, StoredDocument
from app.models.claim import (
    AMENDED_BY_MEMBER,
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_DRAFT,
    member_visible_claims,
)
from app.models.claim_message import EVENT_AMENDED, EVENT_SUBMITTED
from app.models.stored_document import (
    DOC_ENTITY_CLAIM,
    DOC_ENTITY_REFERRAL,
    STORAGE_AVAILABLE,
)
from app.schemas.api import BenefitStatementOut, CoverageLine
from app.schemas.claims import (
    ClaimAmendIn,
    ClaimAnchorOut,
    ClaimCreateIn,
    ClaimIntakeSuggestionOut,
    ClaimList,
    ClaimOut,
    ClaimSetupDocument,
    ClaimTypeOption,
    CoverageOptionsOut,
    DiagnosisOut,
    DiagnosisSearchOut,
    DocSlotOut,
    FlexClaimCategoryOption,
    FlexClaimOptions,
    FxAcknowledgeIn,
    FxQuoteOut,
    HospitalOut,
    InsuredClaimOption,
    StoredDocumentOut,
)
from app.services import ai_gateway
from app.services.ai_breaker import CircuitOpenError
from app.services.ai_extractor import AINotConfiguredError, AIParseError
from app.services.ai_gateway import AIBudgetExceededError
from app.services.claim_document_setups import (
    configured_definitions,
    resolve_setup,
    setup_for_claim,
)
from app.services.claim_episodes import anchor_out, eligible_anchors
from app.services.claim_fx import (
    FX_STATE_CONVERTED,
    apply_conversion,
    build_quote,
    fx_state,
)
from app.services.claim_intake import (
    ALLOWED_CURRENCIES,
    HOSPITALISATION_SLOTS_BY_SECTOR,
    ClaimScopeDefinition,
    anchor_mode_for,
    assert_documents_satisfy_slots,
    claim_profile_for,
    claim_scope_definitions,
    claim_scope_key,
    person_employee_ids,
    referral_letters_for,
    requires_doctor_name,
    sector_scope_code,
    sector_scope_codes,
    supports_stay_dates,
)
from app.services.claim_intake_suggest import build_intake_suggestion
from app.services.claim_messages import post_system_message
from app.services.claims import (
    MEMBER_AMENDABLE_FIELDS,
    amendment_summary,
    apply_claim_amendment,
    assert_claim_revision,
    assert_member_may_amend,
    attach_document,
    audit_cells,
    claim_documents,
    claim_period_window,
    claim_to_out,
    claims_to_out,
    create_claim,
    delete_documents,
    delete_stored_document,
    load_member_claim,
    lock_claim_for_mutation,
    stamp_document_amendment,
    submit_claim,
    supersede_review_for_amendment,
)
from app.services.claims_review.queue import (
    enqueue_amended_claim_review,
    enqueue_claim_review,
)
from app.services.doc_images import DocImageError, vision_blocks_for_document
from app.services.enrollment_products import resolve_products_by_codes
from app.services.fx import POLICY_CURRENCY
from app.services.insurer_listings import member_id_for_insurer
from app.services.member_access import Capability
from app.services.member_statement import build_member_statement
from app.services.product_insurer import insurer_map
from app.services.sg_diagnoses import search_diagnoses
from app.services.sg_hospitals import hospital_directory

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/portal/claims",
    tags=["portal-claims"],
    dependencies=[Depends(get_current_member)],
)

# Coverage options live under /portal but belong to the claims flow — separate
# router so both register cleanly in main.py.
options_router = APIRouter(
    prefix="/portal",
    tags=["portal-claims"],
    dependencies=[Depends(get_current_member)],
)


def _own_claim(db: Session, claim_id: str, employee_id: str) -> Claim:
    """Delegates to `services.claims.load_member_claim` — the ONE loader shared
    with `portal_messages.py`. Kept as a local alias so the call sites here read
    unchanged."""
    return load_member_claim(db, claim_id, employee_id)


def _active_year(db: Session, member: CurrentMember) -> PolicyYear:
    year = active_policy_year(db, member.client_id)
    if year is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active coverage")
    return year


@options_router.get("/coverage-options", response_model=CoverageOptionsOut)
def coverage_options(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> CoverageOptionsOut:
    """What the member may claim against — drives the claim-form pickers."""
    year = _active_year(db, member)
    employee = resolve_member_employee(
        db, member, requires=Capability.CLAIM, year=year
    )
    statement = build_member_statement(db, employee)
    return build_coverage_options(db, statement, employee, year)


def _slots(documents: Sequence[ClaimSetupDocument]) -> list[DocSlotOut]:
    return [
        DocSlotOut(
            key=document.key,
            label=document.display,
            instructions=document.instructions,
        )
        for document in documents
    ]


def _claim_type_option(
    db: Session, client_id: str, line: CoverageLine, scope: ClaimScopeDefinition
) -> ClaimTypeOption:
    """One dropdown entry with its required-document slots.

    Every GHS subclaim has an independently configurable government/private
    leaf. Unlisted providers resolve to private at submit, so that leaf is the
    default while the form switches to government for a known provider.
    """
    by_sector = None
    if sector_scope_codes(scope.code):
        by_sector = {
            sector: _slots(
                resolve_setup(
                    db,
                    client_id,
                    claim_kind="insured",
                    claim_key=line.product_code,
                    scope_code=sector_scope_code(scope.code, sector),
                    sub_type=scope.sub_type,
                ).documents
            )
            for sector in HOSPITALISATION_SLOTS_BY_SECTOR
        }
    documents = (
        by_sector["private"]
        if by_sector is not None
        else _slots(
            resolve_setup(
                db,
                client_id,
                claim_kind="insured",
                claim_key=line.product_code,
                scope_code=scope.code,
                sub_type=scope.sub_type,
            ).documents
        )
    )
    return ClaimTypeOption(
        label=scope.label,
        sub_type=scope.sub_type,
        scope_code=scope.code,
        scope_key=claim_scope_key("insured", line.product_code, scope.code),
        requires_doctor_name=requires_doctor_name(line.product_code, scope.sub_type),
        supports_stay_dates=supports_stay_dates(line.product_code, scope.sub_type),
        anchor_mode=anchor_mode_for(line.product_code, scope.sub_type),
        doc_slots=documents,
        doc_slots_by_sector=by_sector,
    )


def build_coverage_options(
    db: Session,
    statement: BenefitStatementOut,
    employee: Employee,
    year: PolicyYear,
) -> CoverageOptionsOut:
    """Shared by the live portal endpoint above and the broker employee-view
    preview (`portal_preview.py`) so the two can never drift.

    `db` + `employee` are needed to resolve each product's insurer and the
    member's ID with that insurer (roster `insurer_member_ids`), shown read-only
    on the form for the selected claim type. A dependant claimant falls back to
    the policyholder's ID (same convention as the panel cards)."""
    attrs = employee.attribute_values or {}
    # What the member may date a claim, resolved by the SAME function submit
    # enforces — so the form can state the bound instead of surfacing a 422
    # after the member has filled everything in. A leaver's window ends on their
    # last day (`services/claims.py::claim_period_window`).
    #
    # **A window that refuses every date is served as NO window**, and its claim
    # options are dropped with it. `is_empty` is reachable while the member
    # still holds CLAIM — a run-off leaver who left in June against a flex
    # scheme starting 15 July — and serving `start`/`end` verbatim there hands
    # the date input `min > max`, so the form refuses every date with a sentence
    # naming a range that runs backwards. Nothing to pick and one plain reason
    # beats a control that cannot be satisfied.
    insured_window = claim_period_window(db, year, CLAIM_KIND_INSURED, employee)
    insured_open = not insured_window.is_empty
    claim_block = None if insured_open else insured_window.empty_note
    # Resolve every coverage product's insurer in ONE batch (not per line) so
    # the member's insurer ID can be shown for the selected claim type.
    products_by_code = resolve_products_by_codes(
        db, year, [line.product_code for line in statement.coverage]
    )
    insurers_by_product = insurer_map(db, year.id, products_by_code.values())
    insured = []
    for line in statement.coverage if insured_open else ():
        profile = claim_profile_for(line.product_code)
        # Products settled outside the claim form (Major Medical top-up, term
        # life / personal accident / critical illness) never appear in the
        # claim-type picker.
        if not profile.member_claimable:
            continue
        base_label = (
            profile.claim_type_label or line.product_name or line.product_code
        )
        # Shared scope catalogue: the broker settings screen calls the same
        # builder over every plan schedule, while this member view supplies the
        # one schedule the member actually holds.
        claim_types = [
            _claim_type_option(db, employee.client_id, line, scope)
            for scope in claim_scope_definitions(
                line.product_code, base_label, [line.benefit_schedule]
            )
        ]
        product = products_by_code.get(line.product_code)
        insurer = (
            insurers_by_product.get(product.id) if product is not None else None
        )
        member_id = member_id_for_insurer(attrs, insurer)
        insured.append(
            InsuredClaimOption(
                product_code=line.product_code,
                product_name=line.product_name,
                plan_code=line.plan_code,
                annual_policy_limit=line.annual_policy_limit,
                covers_dependants=line.covers_dependants,
                covered_dependant_ids=[d.id for d in line.covered_dependants],
                insurer=insurer,
                insurer_member_id=member_id or None,
                sub_types=list(profile.sub_types),
                requires_referral=profile.requires_referral,
                diagnosis_group=profile.diagnosis_group,
                diagnosis_required=profile.diagnosis_required,
                category=profile.category,
                claim_types=claim_types,
            )
        )

    flex = None
    if statement.flex is not None:
        flex_window = claim_period_window(db, year, CLAIM_KIND_FLEX, employee)
        if flex_window.is_empty:
            # Withheld for the same reason as the insured kind above. Reported
            # only when insured had nothing to say — the two share one cause
            # (cover that ended before the period began), so a second sentence
            # would only restate it.
            claim_block = claim_block or flex_window.empty_note
        else:
            flex_categories = [
                FlexClaimCategoryOption(
                    name=c.name,
                    sub_limit=c.sub_limit,
                    note=c.note,
                    doc_slots=_slots(
                        resolve_setup(
                            db,
                            employee.client_id,
                            claim_kind="flex",
                            claim_key=c.name,
                            scope_code="standard",
                        ).documents
                    ),
                )
                for c in statement.flex.benefit_categories
                if c.claimable
            ]
            flex = FlexClaimOptions(
                currency=statement.flex.currency,
                wallet_amount=statement.flex.wallet_amount,
                flex_balance=statement.flex.flex_balance,
                categories=flex_categories,
                # Kept for older clients. New clients read the selected
                # category's independent list above.
                doc_slots=(flex_categories[0].doc_slots if flex_categories else []),
                # The flex scheme's own effective window, member-clamped. Served
                # separately because it genuinely differs — CDL's scheme starts
                # 15 Jul against a January year — and the form used the
                # policy-year span for both kinds, so a flex claim inside the
                # year but before the scheme started passed the form and was
                # refused at submit.
                claimable_from=flex_window.start.isoformat(),
                claimable_to=flex_window.end.isoformat(),
            )

    return CoverageOptionsOut(
        policy_year_start=year.start_date.isoformat(),
        policy_year_end=year.end_date.isoformat(),
        claimable_from=insured_window.start.isoformat() if insured_open else None,
        claimable_to=insured_window.end.isoformat() if insured_open else None,
        insured=insured,
        flex=flex,
        claim_block=claim_block,
        dependants=[
            {"id": d.id, "name": d.name, "relationship": d.relationship}
            for d in statement.dependants
        ],
        currencies=list(ALLOWED_CURRENCIES),
        policy_currency=POLICY_CURRENCY,
        hospitals=[HospitalOut(**h) for h in hospital_directory()],
    )


# How many documents one autofill request may carry — a member can upload the
# full set for one claim (e.g. tax invoice + itemised bill + discharge summary)
# so the AI reads across all of them (the diagnosis is on the discharge summary,
# the amount on the invoice). Kept small to bound AI spend per request.
MAX_INTAKE_FILES = 3

# Provider burst-throttle recovery: three vision extractions back-to-back can
# trip a 429 on low-quota accounts, and losing the tail of the set silently
# degrades multi-invoice detection (the later invoices just ride along as
# "additional documents"). One short backoff-retry per file recovers a
# transient burst throttle without turning an interactive request into a long
# hang — a per-minute quota won't reopen inside any in-request wait, so a
# longer backoff would just pin the worker (and its DB connection) for nothing.
# The first file that stays throttled stops the set, so the worst-case added
# wait is bounded to RETRIES x BACKOFF per request.
INTAKE_THROTTLE_RETRIES = 1
INTAKE_THROTTLE_BACKOFF_SECONDS = 8.0


async def _extract_with_throttle_retry(
    db: Session,
    *,
    client_id: str,
    policy_year_id: str,
    sha256: str,
    blocks: list[dict[str, Any]],
    file_name: str,
) -> ai_gateway.ClaimExtractionResult:
    """`ai_gateway.extract_claim_document` with a bounded async backoff on
    provider throttling (429). Any other error propagates unchanged. The DB
    session is rolled back before each sleep so the pooled connection is
    RELEASED during the wait (never held idle / idle-in-transaction)."""
    for attempt in range(INTAKE_THROTTLE_RETRIES + 1):
        try:
            # Offload the BLOCKING provider call to a worker thread. This handler
            # is `async def`, so calling the sync gateway inline would freeze the
            # whole event loop for the multi-second AI call and serialize every
            # concurrent upload through one worker. The awaiting coroutine never
            # touches `db` while the thread runs, so the single Session is only
            # ever used by one thread at a time. (The in-gateway concurrency
            # limiter then bounds how many such threads run at once.)
            return await asyncio.to_thread(
                ai_gateway.extract_claim_document,
                db,
                client_id=client_id,
                policy_year_id=policy_year_id,
                sha256=sha256,
                blocks=blocks,
                file_name=file_name,
            )
        except RateLimitError:
            if attempt >= INTAKE_THROTTLE_RETRIES:
                raise
            db.rollback()  # return the connection to the pool during the wait
            await asyncio.sleep(INTAKE_THROTTLE_BACKOFF_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


@options_router.post("/claims/intake", response_model=ClaimIntakeSuggestionOut)
@limiter.limit("20/minute")
async def extract_claim_intake(
    request: Request,
    files: list[UploadFile] = File(...),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimIntakeSuggestionOut:
    """Read up to three uploaded claim documents and return field SUGGESTIONS.

    Each document is extracted with the SAME AI call the post-submit review uses
    (cache key = the document's SHA-256), so this warms the cache and the later
    review re-extract is a free hit. Readings are merged so the amount from an
    invoice and the diagnosis from a discharge summary both fill the form, and
    each document is classified so the form can drop it into the right required-
    document slot. Nothing is persisted (no Claim, no StoredDocument) beyond an
    audit — the member reviews/edits before submit. When the AI is unavailable
    it degrades to `available=False` and the form stays manual.
    """
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "No file uploaded.")
    if len(files) > MAX_INTAKE_FILES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Upload at most {MAX_INTAKE_FILES} documents at a time.",
        )

    year = _active_year(db, member)
    employee = resolve_member_employee(
        db, member, requires=Capability.CLAIM, year=year
    )

    extractions: list[dict[str, Any]] = []
    for upload_index, upload in enumerate(files):
        # Stream to a temp file with the size cap enforced DURING read (never
        # buffer an over-cap upload in memory), then read the bounded bytes back
        # for the vision blocks + hash. saved_upload also enforces the suffix
        # allowlist.
        async with saved_upload(
            upload, set(DOCUMENT_SUFFIXES), max_bytes=MAX_DOCUMENT_BYTES
        ) as tmp_path:
            data = tmp_path.read_bytes()
            suffix = tmp_path.suffix.lower()
        if not data:
            continue  # skip an empty file rather than failing the whole set
        try:
            blocks = vision_blocks_for_document(data, suffix)
        except DocImageError:
            continue  # unreadable page — skip, keep the rest of the set

        sha256 = hashlib.sha256(data).hexdigest()
        try:
            result = await _extract_with_throttle_retry(
                db,
                client_id=employee.client_id,
                policy_year_id=year.id,
                sha256=sha256,
                blocks=blocks,
                file_name=upload.filename or "receipt",
            )
        except (AINotConfiguredError, AIBudgetExceededError, CircuitOpenError, AIParseError):
            # Service-wide degradation (no provider / over budget / breaker /
            # parser fault) — stop trying more files.
            break
        except RateLimitError:
            # Still throttled after the bounded retry — keep what we already
            # read; the remaining files stay attachable, just not prefilled.
            logger.warning(
                "Claim intake throttled for client %s — continuing with %d of %d documents",
                employee.client_id, len(extractions), len(files),
            )
            db.rollback()
            break
        except Exception:
            # Any other provider fault (bad credentials, network) must NOT 500
            # the member — log for ops and stop.
            logger.exception(
                "Claim intake extraction failed for client %s", employee.client_id
            )
            db.rollback()
            break

        write_member_audit(
            db, member, "claim.intake_extracted", "employee", employee.id,
            after={"sha256": sha256, "cache_hit": result.cache_hit},
            employee_id=employee.id,
        )
        db.commit()  # commit each success so a later failure can't roll it back
        extractions.append(
            {
                "file_name": upload.filename or "receipt",
                # Original upload position — the form joins files to documents on
                # it; skipped (empty/unreadable) files leave gaps, so it can't be
                # re-derived from `extractions` order downstream.
                "upload_index": upload_index,
                "document_type": result.document.get("document_type"),
                "fields": result.document.get("fields", []),
            }
        )

    if not extractions:
        return ClaimIntakeSuggestionOut(
            available=False,
            reason="We couldn't read these documents automatically — please fill in the claim.",
        )

    statement = build_member_statement(db, employee)
    coverage_opts = build_coverage_options(db, statement, employee, year)
    return build_intake_suggestion(
        extractions,
        coverage_opts,
        employee,
        year,
        doc_types=configured_definitions(db, employee.client_id),
    )


@options_router.get("/claim-diagnoses", response_model=DiagnosisSearchOut)
def claim_diagnoses(
    product_code: str | None = Query(default=None),
    q: str = Query(default="", max_length=128),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
) -> DiagnosisSearchOut:
    """Searchable diagnosis catalog scoped to the claim type's setting
    (GP / specialist / hospital / dental). No public SG diagnosis API exists,
    so this serves the bundled ICD-10-based catalog."""
    group = claim_profile_for(product_code).diagnosis_group if product_code else None
    hits = search_diagnoses(group, q, limit=limit)
    return DiagnosisSearchOut(
        group=group,
        items=[DiagnosisOut(label=d.label, icd10=d.icd10) for d in hits],
    )


@options_router.get("/fx-quote", response_model=FxQuoteOut)
@limiter.limit("60/minute")
def my_fx_quote(
    request: Request,
    currency: str = Query(min_length=3, max_length=8),
    amount: float = Query(gt=0, le=1_000_000),
    on: date = Query(description="The date on the receipt."),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> FxQuoteOut:
    """What a foreign bill is worth in the policy currency, while the form is
    still being filled in — so the member is shown the figure they will actually
    be reimbursed against BEFORE they commit to it, not after.

    Deliberately not scoped to an employee: it reads no member data and returns
    none. An exchange rate is public market data, so `resolve_member_employee`
    would be gating a public fact behind a leaver check and refusing a lawful
    claimant a number they can look up anywhere. Membership of the portal is the
    gate; the rate limit is what stops it being a free FX proxy.

    Also the CACHE WARMER for submit: the rate for a past date never changes, so
    by the time the claim is sent the figure is already local and the retry
    budget has been spent here, in front of a spinner, rather than there.

    **Which is why this read endpoint commits.** `get_db` only closes the
    session, so without an explicit commit the `fx_rates` row `fx.quote` just
    fetched is discarded on the way out — the cache never warms, and the member
    pays the full upstream budget again on create AND on submit, inside requests
    they are waiting on. The only thing in the session is that cache row.
    """
    out = build_quote(db, currency=currency, amount=amount, on=on)
    db.commit()
    return out


@options_router.get("/claim-anchors", response_model=list[ClaimAnchorOut])
def list_my_claim_anchors(
    mode: str = Query(..., pattern="^(admission|sp_course)$"),
    dependant_id: str | None = Query(default=None),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[ClaimAnchorOut]:
    """The earlier visits this claim may be a follow-up to.

    `dependant_id` selects the CLAIMANT, not a data scope — it is matched
    against the anchor's own claimant so a spouse's admission can't anchor the
    member's consult, and everything is scoped to the employee
    `resolve_member_employee` resolves from the token. An id belonging to
    someone else therefore returns an empty list rather than anything about
    them.
    """
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    return [
        anchor_out(c)
        for c in eligible_anchors(
            db, employee, mode=mode, dependant_id=dependant_id or None
        )
    ]


@options_router.get("/referral-letters", response_model=list[StoredDocumentOut])
def list_my_referral_letters(
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> list[StoredDocumentOut]:
    """The member's own referral letters (reusable across specialist claims).

    Across benefit YEARS — see `claim_intake.referral_letters_for`. A letter that
    dropped out of this list at renewal would be re-requested from a member we
    already hold it for, and the anchor picker would offer a course whose letter
    the control could not display.
    """
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    return [
        StoredDocumentOut.model_validate(d)
        for d in referral_letters_for(db, employee)
    ]


@options_router.post(
    "/referral-letters",
    response_model=StoredDocumentOut,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("20/minute")
async def upload_my_referral_letter(
    request: Request,
    file: UploadFile = File(...),
    # The date printed on the letter. OPTIONAL, and stays optional: a member
    # holding a letter they cannot read a date off must still be able to attach
    # it — the referral requirement is what gates the claim, not our ability to
    # date it. Absent, the age rule simply does not run.
    issued_on: date | None = Form(default=None),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    if issued_on is not None and issued_on > business_today():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A referral letter can't be dated in the future.",
        )
    doc = await attach_document(
        db,
        client_id=employee.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_REFERRAL,
        entity_id=employee.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
        issued_on=issued_on,
    )
    write_member_audit(
        db, member, "referral_letter.uploaded", "stored_document", doc.id,
        after={
            "file_name": doc.file_name,
            "sha256": doc.sha256,
            "issued_on": issued_on.isoformat() if issued_on else None,
        },
        employee_id=employee.id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)


@options_router.delete(
    "/referral-letters/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_my_referral_letter(
    doc_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> None:
    """Remove one of the member's own referral letters. Refuses (409) while a
    claim still rides on it, so deleting can't strand a claim's
    `referral_document_id`."""
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    doc = db.get(StoredDocument, doc_id)
    if (
        doc is None
        or doc.entity_type != DOC_ENTITY_REFERRAL
        or doc.storage_state != STORAGE_AVAILABLE
        # Same person-wide scope the list is served under, or a member could see
        # a letter carried over from last year and not be able to remove it.
        or doc.entity_id not in person_employee_ids(db, employee)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Referral letter not found")
    in_use = db.scalar(
        select(func.count(Claim.id)).where(Claim.referral_document_id == doc_id)
    )
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "referral_in_use",
                "message": "This referral letter is attached to a claim and "
                "can't be deleted.",
            },
        )
    delete_stored_document(db, doc)
    write_member_audit(
        db, member, "referral_letter.deleted", "stored_document", doc_id,
        employee_id=employee.id,
    )
    db.commit()


@router.get("", response_model=ClaimList)
def list_my_claims(
    status_filter: str | None = Query(default=None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimList:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    conditions = [
        Claim.employee_id == employee.id,
        Claim.policy_year_id == employee.policy_year_id,
        # The member's own submissions only.
        #
        # This filters on ORIGIN, not on `case_type`, and the difference is the
        # whole point: filtering on case type would make a claim the member
        # submitted DISAPPEAR from their portal the moment an assessor
        # reclassified it as a LOG case — they'd watch their own record vanish
        # mid-review with no notice and no way to ask about it. Origin hides
        # only the cases they never knew about.
        #
        # `api/v1/portal_preview.py` must apply the identical filter — the
        # preview is asserted to return exactly what the member sees.
        member_visible_claims(),
    ]
    if status_filter:
        conditions.append(Claim.status == status_filter)
    total = db.scalar(select(func.count(Claim.id)).where(*conditions)) or 0
    rows = db.execute(
        select(Claim)
        .where(*conditions)
        .order_by(Claim.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()
    return ClaimList(
        total=total,
        offset=offset,
        limit=limit,
        items=claims_to_out(db, list(rows)),
    )


@router.post("", response_model=ClaimOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("20/minute")
def create_my_claim(
    request: Request,
    body: ClaimCreateIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    claim = create_claim(
        db, employee, body, submitted_by_member_id=member.member_account_id
    )
    write_member_audit(
        db, member, "claim.drafted", "claim", claim.id,
        after={"claim_kind": claim.claim_kind, "amount": claim.amount_claimed},
        employee_id=employee.id,
    )
    db.commit()
    return claim_to_out(db, claim)


@router.get("/{claim_id}", response_model=ClaimOut)
def get_my_claim(
    claim_id: str,
    request: Request,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    claim = _own_claim(db, claim_id, employee.id)
    out = claim_to_out(db, claim)
    write_member_audit(
        db,
        member,
        "claim.view",
        "claim",
        claim.id,
        employee_id=employee.id,
        request=request,
    )
    db.commit()
    return out


@router.get("/{claim_id}/documents/{doc_id}/download")
def download_my_claim_document(
    claim_id: str,
    doc_id: str,
    request: Request,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> Response:
    employee = resolve_member_employee(db, member, requires=Capability.RECORD)
    claim = _own_claim(db, claim_id, employee.id)
    doc = db.get(StoredDocument, doc_id)
    claim_attachment = (
        doc is not None
        and doc.entity_type == DOC_ENTITY_CLAIM
        and doc.entity_id == claim.id
    )
    referral = (
        doc is not None
        and doc.id == claim.referral_document_id
        and doc.entity_type == DOC_ENTITY_REFERRAL
        and doc.entity_id in person_employee_ids(db, employee)
    )
    if (
        doc is None
        or doc.client_id != claim.client_id
        or doc.storage_state != STORAGE_AVAILABLE
        or not (claim_attachment or referral)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    try:
        content = get_storage().read(doc.storage_path)
    except FileNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Document bytes are no longer available",
        ) from None
    write_member_audit(
        db,
        member,
        "claim.document.download",
        "stored_document",
        doc.id,
        employee_id=employee.id,
        request=request,
    )
    db.commit()
    return Response(
        content=content,
        media_type=doc.mime_type or "application/octet-stream",
        headers={"Content-Disposition": attachment_header(doc.file_name)},
    )


@router.post("/{claim_id}/documents", response_model=StoredDocumentOut)
@limiter.limit("20/minute")
async def upload_my_claim_document(
    request: Request,
    claim_id: str,
    file: UploadFile = File(...),
    doc_type: str | None = Form(default=None),
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> StoredDocumentOut:
    # RESPOND, not CLAIM: attaching the document a broker asked for is how a
    # `needs_info` claim gets answered, and a member whose run-off has expired
    # must still be able to do it (`services/member_access.py`).
    employee = resolve_member_employee(db, member, requires=Capability.RESPOND)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    # Same split as `submit_my_claim`: adding evidence to a DRAFT is building a
    # new claim, so it additionally needs CLAIM. `MEMBER_EDITABLE_STATUSES`
    # includes `draft`, so RESPOND alone let a `settling` member pile documents
    # onto a stale draft they can neither submit nor delete.
    if claim.status == CLAIM_STATUS_DRAFT:
        assert_member_capability(db, employee, Capability.CLAIM)
    # `assert_member_may_amend`, not a raw status check: `member_editability` is
    # the ONE owner of "may the claimant still change this claim", and the two
    # answers differ — a portal claim reclassified to a LOG case is refused by
    # the amend and delete endpoints but its status is still in
    # `MEMBER_EDITABLE_STATUSES`, so a raw check let a member keep posting
    # documents to a case the broker had taken over (bumping `revision` and
    # superseding the review each time).
    assert_member_may_amend(claim)
    # The slot tag must be a known slot key — the submit-time requirement
    # check trusts these values.
    valid_slots = {document.key for document in setup_for_claim(db, claim).documents}
    if doc_type is not None and doc_type not in valid_slots:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"'{doc_type}' is not a recognised document type.",
        )
    doc = await attach_document(
        db,
        client_id=claim.client_id,
        broker_firm_id=member.broker_firm_id,
        entity_type=DOC_ENTITY_CLAIM,
        entity_id=claim.id,
        file=file,
        uploaded_by_member_id=member.member_account_id,
        doc_type=doc_type,
    )
    # Adding evidence moves the claim just as removing it does — a verdict that
    # ran without this document is describing a different claim, and a broker
    # who read the claim before it landed must not decide as if they had seen
    # it. No-op on a draft.
    stamp_document_amendment(db, claim)
    enqueue_amended_claim_review(db, claim, member.broker_firm_id)
    write_member_audit(
        db, member, "claim.document_added", "claim", claim.id,
        after={
            "file_name": doc.file_name,
            "sha256": doc.sha256,
            "doc_type": doc.doc_type,
            "revision": claim.revision,
        },
        employee_id=employee.id,
    )
    db.commit()
    return StoredDocumentOut.model_validate(doc)


@router.patch("/{claim_id}", response_model=ClaimOut)
@limiter.limit("20/minute")
def amend_my_claim(
    request: Request,
    claim_id: str,
    body: ClaimAmendIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    """Correct a claim the broker has not yet decided.

    RESPOND, plus CLAIM on a draft — the same split as `submit_my_claim`, for
    the same reason: correcting an open claim is acting on something that
    already exists (exactly what the `settling` state exists to keep possible),
    while finishing a draft is starting a new claim and must not be available to
    someone past their run-off.

    The merged claim is re-validated by the same chain submit runs, so an
    amendment can only ever leave behind a claim that would pass submit — the
    broker's queue can never hold an invalid row.
    """
    employee = resolve_member_employee(db, member, requires=Capability.RESPOND)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    if claim.status == CLAIM_STATUS_DRAFT:
        assert_member_capability(db, employee, Capability.CLAIM)
    assert_member_may_amend(claim)
    assert_claim_revision(claim, body.expected_revision)

    before, after = apply_claim_amendment(
        db, claim, body, employee,
        allowed=MEMBER_AMENDABLE_FIELDS,
        actor=AMENDED_BY_MEMBER,
    )
    supersede_review_for_amendment(db, claim)
    enqueue_amended_claim_review(db, claim, member.broker_firm_id)
    # Their own record of what they did, and what tells the broker the claim
    # they are holding has moved. A DRAFT has no thread — nothing has been sent.
    if claim.status != CLAIM_STATUS_DRAFT:
        post_system_message(
            db, claim, EVENT_AMENDED, note=amendment_summary(after)
        )
    write_member_audit(
        db, member, "claim.amended", "claim", claim.id,
        before=audit_cells(before),
        after={**audit_cells(after), "revision": claim.revision},
        employee_id=employee.id,
    )
    db.commit()
    return claim_to_out(db, claim)


@router.delete(
    "/{claim_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_my_claim_document(
    claim_id: str,
    doc_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> None:
    """Remove a document from a claim the broker has not yet decided.

    Correcting a claim usually means replacing the wrong receipt, so this is
    the other half of the amendment surface and shares its gate exactly.

    **On a non-draft claim the remaining documents must still satisfy every
    required slot.** A submitted claim is in front of an assessor, and deleting
    its only receipt would park a claim there that can never be progressed. A
    draft may go empty — it is in front of nobody, and submit will ask for the
    documents when it is sent.
    """
    employee = resolve_member_employee(db, member, requires=Capability.RESPOND)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    if claim.status == CLAIM_STATUS_DRAFT:
        assert_member_capability(db, employee, Capability.CLAIM)
    assert_member_may_amend(claim)

    doc = db.get(StoredDocument, doc_id)
    # This claim's OWN attachments only. The member-level referral letter has
    # its own endpoint with its own in-use guard — reaching it through here
    # would strand every other claim riding on the same letter.
    if (
        doc is None
        or doc.entity_type != DOC_ENTITY_CLAIM
        or doc.entity_id != claim.id
        or doc.storage_state != STORAGE_AVAILABLE
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    if claim.status != CLAIM_STATUS_DRAFT:
        remaining = [d for d in claim_documents(db, claim) if d.id != doc.id]
        try:
            required = setup_for_claim(db, claim).documents
            assert_documents_satisfy_slots(
                [document.key for document in required],
                remaining,
                labels={document.key: document.display for document in required},
            )
        except HTTPException as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={
                    "code": "documents_required",
                    "message": (
                        "Removing this would leave the claim without a document "
                        "it needs. Add the replacement first, then remove this "
                        "one."
                    ),
                    "detail": exc.detail,
                },
            ) from exc

    delete_stored_document(db, doc)
    # Evidence IS what a verdict is about, so removing a document invalidates
    # one exactly as changing a figure does.
    stamp_document_amendment(db, claim)
    enqueue_amended_claim_review(db, claim, member.broker_firm_id)
    write_member_audit(
        db, member, "claim.document_removed", "claim", claim.id,
        before={"file_name": doc.file_name, "sha256": doc.sha256,
                "doc_type": doc.doc_type},
        after={"revision": claim.revision},
        employee_id=employee.id,
    )
    db.commit()


@router.delete("/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_draft_claim(
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> None:
    employee = resolve_member_employee(db, member, requires=Capability.CLAIM)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    if claim.status != "draft":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a draft claim can be deleted."
        )
    delete_documents(db, DOC_ENTITY_CLAIM, claim.id)
    write_member_audit(
        db, member, "claim.deleted", "claim", claim.id, employee_id=employee.id
    )
    db.delete(claim)
    db.commit()


@router.post("/{claim_id}/confirm-conversion", response_model=ClaimOut)
@limiter.limit("20/minute")
def confirm_my_conversion(
    request: Request,
    claim_id: str,
    body: FxAcknowledgeIn,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    """Accept the policy-currency figure this claim converts to.

    Its own endpoint rather than a field on the amendment, because accepting a
    figure is not correcting a claim: nothing about the claim changes. Routed
    through `amend_my_claim` it would bump `revision`, supersede the AI review
    and post the member a notice telling them they had changed something.

    RESPOND, not CLAIM — the same capability answering a `needs_info` needs.
    Confirming the conversion on a claim already filed is finishing something in
    flight, and a member whose run-off has expired must still be able to.

    Re-prices before stamping, so what is being consented to is the current
    figure and not one that has since moved. If it HAS moved, this returns the
    claim carrying the new one, unacknowledged — the form shows it and asks
    again rather than recording consent to a number nobody saw.
    """
    employee = resolve_member_employee(db, member, requires=Capability.RESPOND)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    assert_member_may_amend(claim)
    apply_conversion(db, claim)
    converted_amount = claim.amount_converted
    if fx_state(claim) == FX_STATE_CONVERTED and (
        converted_amount is not None
        and (
            body.converted_amount is None
            or abs(float(body.converted_amount) - float(converted_amount)) <= 0.005
        )
    ):
        claim.fx_acknowledged_at = datetime.now(UTC)
        write_member_audit(
            db, member, "claim.conversion_confirmed", "claim", claim.id,
            after={
                "currency": claim.currency,
                "amount_claimed": claim.amount_claimed,
                "amount_converted": claim.amount_converted,
                "fx_rate": claim.fx_rate,
            },
            employee_id=employee.id,
        )
    db.commit()
    return claim_to_out(db, claim)


@router.post("/{claim_id}/submit", response_model=ClaimOut)
@limiter.limit("10/minute")
def submit_my_claim(
    request: Request,
    claim_id: str,
    member: CurrentMember = Depends(get_current_member),
    db: Session = Depends(get_db),
) -> ClaimOut:
    # A submit is two different acts down one route. Answering a `needs_info`
    # is RESPOND — the whole point of the `settling` state. Finishing a DRAFT
    # is starting a new claim, so it additionally needs CLAIM, which is what
    # stops a member past their run-off filing a claim from a stale draft.
    employee = resolve_member_employee(db, member, requires=Capability.RESPOND)
    claim = lock_claim_for_mutation(db, _own_claim(db, claim_id, employee.id))
    if claim.status == CLAIM_STATUS_DRAFT:
        assert_member_capability(db, employee, Capability.CLAIM)
    submit_claim(
        db, claim, employee, submitted_by_member_id=member.member_account_id
    )
    if not member.broker_firm_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Claim tenant is not configured.")
    enqueue_claim_review(
        db, claim, member.broker_firm_id, supersede=True
    )
    # The "we have it" notice. Posted on every submit INCLUDING a needs_info
    # resubmission — the member has just been asked for something and sent it,
    # which is exactly when they need the acknowledgement most.
    post_system_message(db, claim, EVENT_SUBMITTED)
    write_member_audit(
        db, member, "claim.submitted", "claim", claim.id,
        after={
            "claim_kind": claim.claim_kind,
            "product_code": claim.product_code,
            "flex_category_name": claim.flex_category_name,
            "amount": claim.amount_claimed,
            "currency": claim.currency,
        },
        employee_id=employee.id,
    )
    db.commit()
    return claim_to_out(db, claim)
