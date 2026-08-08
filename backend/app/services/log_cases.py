"""LOG cases — a broker-entered claim category.

A LOG case is an ordinary `Claim` row carrying ``case_type="log"``. It enters
the SAME queue at ``submitted``, moves through the SAME statuses, is decided by
the SAME endpoint and counts against the member's limit the SAME way. There is
no separate lifecycle and no separate money path — which is what keeps this
small.

Two things are genuinely different, and both live here:

1. **What is validated.** The member pipeline's intake rules exist to make a
   receipt-driven claim comparable to its documents. A LOG request arrives by
   email carrying an estimate at best, so applying them would make the form
   unfillable. `assert_log_valid` keeps the checks that are about the *member*
   (coverage exists, dependant is covered, date in period, currency) and drops
   the ones that are about the *evidence* (sub-type, diagnosis, referral letter,
   document slots, the "attach a receipt" rule). See the module docstring of
   `claim_intake.py` for what is being skipped.

2. **Reclassification.** `set_case_type` moves a case between the two
   categories in place — one row, one field, fully reversible — which is only
   possible because both categories live in the same table.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Claim, Dependant, Employee, PolicyYear
from app.models.claim import (
    CASE_TYPE_CLAIM,
    CASE_TYPE_LOG,
    CLAIM_KIND_FLEX,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_SUBMITTED,
    LOG_CLAIM_TYPE,
    ORIGIN_BROKER,
    RELABELLABLE_STATUSES,
)
from app.models.policy_year import PolicyYearStatus
from app.schemas.claims import LogCaseCreateIn
from app.services.claim_intake import ALLOWED_CURRENCIES, normalize_sub_type
from app.services.claim_settlement import mint_reference_no
from app.services.claims import assert_coverage_claimable, assert_incurred_in_period
from app.services.member_statement import build_member_statement

# How a request reached the assessor. Display/reporting only — nothing branches
# on it, so an unrecognised value can never change behaviour.
RECEIVED_VIA = ("email", "phone", "hr", "hospital", "other")

# Bounds the reclassification trail. A case being relabelled more than this many
# times is a workflow problem, not a data-retention one, and an unbounded list
# on an untyped JSON column grows without anything ever trimming it.
_MAX_CONVERSIONS = 20


def intake_field(claim: Claim, key: str) -> Any:
    """One provenance value off `intake_meta`, read defensively.

    `intake_meta` is untyped JSON: legacy rows carry None, and a hand-edited row
    can carry anything at all. Every read goes through here so a malformed value
    renders as absent rather than 500-ing the claims queue.
    """
    meta = claim.intake_meta
    if not isinstance(meta, dict):
        return None
    value = meta.get(key)
    return value if value not in ("", {}) else None


def intake_date(claim: Claim, key: str) -> date | None:
    """A provenance value that should be an ISO date. Returns None on anything
    unparseable rather than raising — see `intake_field`."""
    raw = intake_field(claim, key)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _conversions(claim: Claim) -> list[dict[str, Any]]:
    trail = intake_field(claim, "conversions")
    if not isinstance(trail, list):
        return []
    return [entry for entry in trail if isinstance(entry, dict)]


def assert_log_valid(
    db: Session,
    employee: Employee,
    claim: Claim,
    year: PolicyYear,
) -> None:
    """Validate a LOG case. Everything here is about the MEMBER, never the
    evidence — see the module docstring for what is deliberately absent."""
    if claim.claim_kind == CLAIM_KIND_INSURED and not claim.product_code:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A LOG case must name the coverage it draws on.",
        )
    if claim.claim_kind == CLAIM_KIND_FLEX and not claim.flex_category_name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A flex LOG case must name the claimable benefit category.",
        )
    if claim.currency.upper() not in ALLOWED_CURRENCIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{claim.currency}' is not a supported claim currency.",
        )
    if claim.dependant_id:
        dep = db.get(Dependant, claim.dependant_id)
        if dep is None or dep.employee_id != employee.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Dependant not found")
        if dep.status != "active":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "This dependant is pending approval and can't be claimed for yet.",
            )
    assert_incurred_in_period(db, year, claim)
    # Coverage must exist on the member's own resolved statement, and a named
    # dependant must be covered under it. The `member_claimable` gate inside
    # this helper is exempted for LOG cases (see its comment) — recording a case
    # for an insurer-settled product is exactly what an assessor is for.
    assert_coverage_claimable(build_member_statement(db, employee), claim)


def create_log_case(
    db: Session,
    employee: Employee,
    body: LogCaseCreateIn,
    year: PolicyYear,
    *,
    user_id: str | None,
) -> Claim:
    """Create a LOG case at `submitted` — no draft, no member submit path.

    Does NOT commit; the caller owns the transaction and the audit entry.
    """
    if year.status != PolicyYearStatus.active:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "LOG cases can only be recorded against the current benefit year.",
        )

    sub_type = normalize_sub_type(body.sub_type)
    claim = Claim(
        client_id=employee.client_id,
        policy_year_id=employee.policy_year_id,
        employee_id=employee.id,
        dependant_id=body.dependant_id,
        case_type=CASE_TYPE_LOG,
        origin=ORIGIN_BROKER,
        created_by_user_id=user_id,
        claim_kind=body.claim_kind,
        product_code=body.product_code,
        flex_category_name=body.flex_category_name,
        claim_type=LOG_CLAIM_TYPE,
        sub_type=sub_type,
        incurred_date=body.incurred_date,
        provider_name=body.provider_name,
        invoice_number=body.invoice_number,
        diagnosis=body.diagnosis,
        remarks=body.remarks,
        amount_claimed=body.amount_claimed,
        currency=body.currency.upper(),
        # Straight into the queue. A LOG case has no draft state: the assessor
        # is recording something that already happened, not composing it.
        status=CLAIM_STATUS_SUBMITTED,
        submitted_at=datetime.now(UTC),
        intake_meta={
            "received_via": body.received_via,
            "received_on": (
                body.received_on.isoformat() if body.received_on else None
            ),
            "requested_by": body.requested_by,
        },
        # Same snapshot shape a member claim carries, so a broker who later
        # attaches documents can run the AI review over this case unchanged.
        form_fields={
            "claim_type": LOG_CLAIM_TYPE,
            "sub_type": sub_type,
            "incurred_date": body.incurred_date.isoformat(),
            "provider_name": body.provider_name,
            "invoice_number": body.invoice_number,
            "diagnosis": body.diagnosis,
            "amount_claimed": body.amount_claimed,
            "currency": body.currency.upper(),
        },
    )
    assert_log_valid(db, employee, claim, year)
    db.add(claim)
    db.flush()
    # A LOG case lands at `submitted` WITHOUT going through `submit_claim`, so
    # it does not pick up a reference on the way in. It still needs one: it is
    # a claim in the register, it is reconciled against the insurer's ledger by
    # that string, and the member whose treatment it covers may well ring up
    # about it. Minting here rather than moving the call into a shared helper
    # keeps both entry points explicit — there are exactly two ways a claim
    # reaches `submitted`, and each mints its own.
    mint_reference_no(db, claim)
    db.flush()
    return claim


def set_case_type(
    claim: Claim,
    *,
    case_type: str,
    reason: str,
    user_id: str | None,
) -> bool:
    """Reclassify a case in place. Returns False when it was already that type.

    Status, documents, messages, amounts and any AI review are left untouched —
    this is a reclassification, not a reset, so nothing here goes through
    `assert_transition`.

    Idempotent on purpose: a double-clicked confirm dialog should not report an
    error after the first click succeeded.
    """
    if claim.case_type == case_type:
        return False
    if claim.status not in RELABELLABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "case_type_locked",
                "message": (
                    f"A {claim.status} case can't be reclassified. Its outcome "
                    "is already recorded."
                ),
            },
        )

    # `claim_type` is deliberately NOT rewritten.
    #
    # It is the DESCRIPTIVE label ("Emergency Accidental Outpatient Treatment")
    # and it is what the MEMBER sees as the title of their own claim. Stamping
    # it "LOG" on reclassification leaked broker vocabulary onto the member's
    # portal — they opened their claims and found one of them renamed to an
    # acronym they have never been given. The category is carried by
    # `case_type`, which the broker queue renders as a badge beside the label;
    # keeping the label also means the reverse direction has nothing to restore.
    trail = [
        *_conversions(claim),
        {
            "from": claim.case_type,
            "to": case_type,
            "at": datetime.now(UTC).isoformat(),
            "by": user_id,
            "reason": reason,
        },
    ]
    meta = dict(claim.intake_meta) if isinstance(claim.intake_meta, dict) else {}
    meta["conversions"] = trail[-_MAX_CONVERSIONS:]
    # Reassign rather than mutate: a JSON column mutated in place is not seen as
    # dirty by SQLAlchemy's default change detection, so the write is silently
    # dropped.
    claim.intake_meta = meta
    claim.case_type = case_type
    return True


def case_type_or_400(value: str | None) -> str | None:
    """Validate a `?case_type=` filter value. None = both categories, which is
    the deliberate default: narrowing it would silently change what every
    existing caller of the claims list receives."""
    if value is None or value == "":
        return None
    if value not in (CASE_TYPE_CLAIM, CASE_TYPE_LOG):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{value}' is not a claim case type.",
        )
    return value
