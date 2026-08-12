"""Claim episodes — the earlier visit a claim continues.

Two claim types are, by definition, continuations:

- a **pre-/post-hospitalisation consult** belongs to a hospital ADMISSION (the
  insurer pays it only because of that admission, and matches the two through
  the attending doctor and the diagnosis);
- a **specialist follow-up** continues the first visit of a course of treatment.

`Claim.related_claim_id` records which one. Everything else this module exists
for — the picker's options, the prefill the form derives from them, the review
rules in `claims_review/rules.py`, the episode column in the register — reads
off that single link.

Design rules that are load-bearing:

- **The link is optional, always.** A member must never be unable to file a
  claim because the admission it follows was settled by Letter of Guarantee, or
  happened before we held their data, or simply cannot be found. `None` is a
  complete answer.
- **The anchor is stored as the ROOT of its episode** (`resolve_anchor_root`),
  so a follow-up of a follow-up still points at the admission and "everything in
  this episode" stays one query.
- **The 18-month lookback bounds the PICKER, not validity.** See
  `resolve_anchor`.
- **A LOG anchor links, it does not prefill.** See `anchor_out`.

Full design: `docs/CLAIM_EPISODES_PLAN.md`.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import today
from app.models import Claim, Dependant, Employee
from app.models.claim import (
    CASE_TYPE_LOG,
    CLAIM_KIND_INSURED,
    CLAIM_STATUS_DRAFT,
    ORIGIN_PORTAL,
)
from app.schemas.claims import ClaimAnchorOut
from app.services.claim_intake import (
    ANCHOR_ADMISSION,
    ANCHOR_MODES,
    ANCHOR_SP_COURSE,
    SUB_TYPE_HOSPITALISATION,
    VISIT_FIRST,
    VISIT_FOLLOW_UP,
    anchor_mode_for,
    is_inpatient_product,
    normalize_sub_type,
    person_employee_ids,
)

# How far back the picker looks. Deliberately NOT the policy year: a January
# consult routinely follows a December admission from the year before, and
# scoping to the active year would empty the picker for exactly the members who
# need it. The claim's own in-period rule is untouched and still applies to the
# consult itself.
ANCHOR_LOOKBACK_DAYS = 550

# The picker is a list a member reads, not a search. Twenty visits is already
# more than anyone scans.
MAX_ANCHORS = 20


def anchor_mode_for_claim(claim: Claim) -> str | None:
    """The anchor THIS claim may carry — the claim-type answer, narrowed.

    A specialist claim's type reports `sp_course` either way (see
    `claim_intake.anchor_mode_for`); only a follow-up may actually name one. A
    first visit that carried an anchor would be claiming to continue a course it
    is the start of.
    """
    mode = anchor_mode_for(
        claim.product_code, claim.sub_type, claim_kind=claim.claim_kind
    )
    if mode == ANCHOR_SP_COURSE and claim.visit_type != VISIT_FOLLOW_UP:
        return None
    return mode


def _matches_mode(anchor: Claim, mode: str) -> bool:
    """Whether a claim is the KIND of visit `mode` anchors to.

    Product type is tested with `is_inpatient_product` rather than a product-code
    list in the query, because that list already exists — in
    `claim_intake._PROFILES` — and a second copy in SQL is a place to forget when
    a product is added. The candidate set is one member over 18 months, so
    filtering in Python costs nothing.
    """
    if mode == ANCHOR_ADMISSION:
        if not (
            anchor.claim_kind == CLAIM_KIND_INSURED
            and is_inpatient_product(anchor.product_code)
        ):
            return False
        # A LOG case IS an admission — that is what an admission-guarantee
        # request is — and it routinely carries no sub-type at all, because the
        # email it was recorded from carried none (`log_cases.assert_log_valid`
        # drops the form's rules). Requiring the sub-type here would hide every
        # guaranteed admission, which is the majority of them.
        if anchor.case_type == CASE_TYPE_LOG:
            return True
        return normalize_sub_type(anchor.sub_type) == SUB_TYPE_HOSPITALISATION
    if mode == ANCHOR_SP_COURSE:
        # The root of a course. A LOG case is excluded: it is not a specialist
        # visit the member attended and it carries no referral to ride on.
        return anchor.origin == ORIGIN_PORTAL and anchor.visit_type == VISIT_FIRST
    return False


class _Claimant:
    """Who a claim is about, resolved ACROSS benefit years.

    `Employee` and `Dependant` rows are per policy YEAR — renewing into a new
    year creates new rows — so "the same person" is not "the same id". An
    admission in December and its post-discharge consults in January are the
    ordinary shape of a pre/post episode and they sit either side of that
    boundary, so matching on `employee_id` alone would empty the picker exactly
    when it is most needed.

    The member's rows come from `claim_intake.person_employee_ids`, which
    applies the same binding rule as `member_access.locate_employee` — the
    account stamp, or an unclaimed row with the same staff id, and never a row
    bound to somebody else. Matching on `(client_id, staff_id)` alone would be
    wrong in a way nothing else here would catch: where a staff id is recycled
    or a roster carries a placeholder, another person's claims become anchor
    options and their diagnosis prefills this member's form.

    A dependant is their normalized national ID — and when they have none, the
    row itself, which can only ever under-offer. That asymmetry is deliberate: a
    wrong cross-year dependant match is the one failure here that puts one
    household member's diagnosis in another's form, so it is the one place that
    refuses to guess.
    """

    __slots__ = ("dependant_ids", "employee_ids", "is_member")

    def __init__(
        self, employee_ids: list[str], dependant_ids: set[str], is_member: bool
    ) -> None:
        self.employee_ids = employee_ids
        self.dependant_ids = dependant_ids
        self.is_member = is_member

    def matches(self, anchor: Claim) -> bool:
        if self.is_member:
            return anchor.dependant_id is None
        return anchor.dependant_id in self.dependant_ids


def _claimant(
    db: Session, employee: Employee, dependant_id: str | None
) -> _Claimant:
    employee_ids = person_employee_ids(db, employee)
    if not dependant_id:
        return _Claimant(employee_ids, set(), is_member=True)
    # Loaded THROUGH the employee rows, never by bare id: `dependant_id` reaches
    # the anchors endpoint as an unvalidated query parameter, and a point-load
    # would read any dependant row in the database — another tenant's included —
    # and use its national ID to widen the match set. Someone else's id resolves
    # to nothing here, which is also the honest answer: they are not a claimant
    # this member may file for.
    dep = db.execute(
        select(Dependant).where(
            Dependant.id == dependant_id,
            Dependant.employee_id.in_(employee_ids),
        )
    ).scalars().one_or_none()
    if dep is None:
        return _Claimant(employee_ids, set(), is_member=False)
    ids = {dep.id}
    if dep.national_id_normalized:
        ids |= set(
            db.execute(
                select(Dependant.id).where(
                    Dependant.employee_id.in_(employee_ids),
                    Dependant.national_id_normalized == dep.national_id_normalized,
                )
            ).scalars()
        )
    return _Claimant(employee_ids, ids, is_member=False)


def eligible_anchors(
    db: Session,
    employee: Employee,
    *,
    mode: str,
    dependant_id: str | None = None,
    exclude_claim_id: str | None = None,
) -> list[Claim]:
    """The earlier visits this member may anchor a new claim to.

    Scoped to `employee` by the caller having resolved it — never to a
    client/employee id taken from the request (`docs/PORTAL.md`).
    """
    if mode not in ANCHOR_MODES:
        return []
    claimant = _claimant(db, employee, dependant_id)
    stmt = (
        select(Claim)
        .where(
            Claim.employee_id.in_(claimant.employee_ids),
            # A draft is the member's own unfinished work, not a visit that
            # happened as far as anything else is concerned.
            Claim.status != CLAIM_STATUS_DRAFT,
            Claim.incurred_date >= today() - timedelta(days=ANCHOR_LOOKBACK_DAYS),
            # Nothing that is itself a continuation: the picker offers roots, so
            # `resolve_anchor_root` has nothing to correct in the ordinary case.
            Claim.related_claim_id.is_(None),
        )
        .order_by(Claim.incurred_date.desc(), Claim.created_at.desc())
    )
    if exclude_claim_id is not None:
        stmt = stmt.where(Claim.id != exclude_claim_id)
    candidates = [
        c
        for c in db.execute(stmt).scalars()
        if claimant.matches(c) and _matches_mode(c, mode)
    ]
    return candidates[:MAX_ANCHORS]


def anchor_out(anchor: Claim, *, for_broker: bool = False) -> ClaimAnchorOut:
    """The projection of an anchor. Redacted for a MEMBER; whole for a broker.

    **A LOG case is not member-visible** (`claim.member_visible_claims`), and
    the member picker is the one place a member sees one at all: the admission
    they are claiming a consult against was settled by Letter of Guarantee and
    never filed by them, so a picker built on their own submissions alone is
    empty in the commonest case there is.

    The carve-out is kept as narrow as it can be. To a member, a LOG anchor
    serves the hospital and the dates — facts they lived through — and
    **nothing a broker wrote**: no amount, no status, no reference number, no
    assessor note, and no diagnosis or doctor either, so nothing broker-entered
    can prefill their form. A LOG anchor LINKS; it does not PREFILL. Their own
    claims carry their own words back, which is what they typed in the first
    place.

    ``for_broker`` lifts the redaction, and exists because applying it to the
    assessor was a straight loss: the broker already has the anchor claim in
    full on their own queue, so blanking its diagnosis bought no privacy and
    cost them the one comparison the episode rules are asking them to make —
    "is this consult really for that admission?" — on precisely the guaranteed
    admissions where they cannot check it anywhere else. `from_records` is
    still served either way: it says the anchor was broker-entered, which is
    context for both audiences rather than something being withheld.

    The MEMBER default is the safe one, so a new caller that forgets the flag
    under-discloses rather than leaking. `portal_preview` must keep the default
    even though a broker is looking: it renders the member's own view, and the
    two payloads are required to be identical.
    """
    redact = anchor.origin != ORIGIN_PORTAL and not for_broker
    return ClaimAnchorOut(
        id=anchor.id,
        provider_name=anchor.provider_name,
        incurred_date=anchor.incurred_date,
        admission_date=anchor.admission_date,
        discharge_date=anchor.discharge_date,
        diagnosis=None if redact else anchor.diagnosis,
        doctor_name=None if redact else anchor.doctor_name,
        referral_document_id=None if redact else anchor.referral_document_id,
        from_records=anchor.origin != ORIGIN_PORTAL,
    )


def resolve_anchor_root(db: Session, anchor: Claim) -> Claim:
    """The head of the anchor's episode.

    Depth-1 by construction: what gets STORED is always a root, so following the
    link once is enough and there is no chain to walk. Defensive rather than
    reachable through the picker, which offers roots only.
    """
    if anchor.related_claim_id is None:
        return anchor
    root = db.get(Claim, anchor.related_claim_id)
    return root if root is not None else anchor


def resolve_anchor(
    db: Session,
    employee: Employee,
    *,
    anchor_id: str | None,
    claim_kind: str,
    product_code: str | None,
    sub_type: str | None,
    visit_type: str | None,
    dependant_id: str | None,
    exclude_claim_id: str | None = None,
) -> str | None:
    """Validate a chosen anchor and return what to store — the same shape as
    `claim_intake.resolve_sp_referral`, and used the same way by both the create
    path and the amendment path.

    Two behaviours worth stating, because both are the difference between a rule
    and a trap:

    **A claim whose type no longer admits an anchor has the link CLEARED, not
    refused.** Amend a pre/post consult onto an ordinary outpatient type and the
    anchor stops meaning anything — 422-ing there would make the claim
    uncorrectable from any surface that does not expose the anchor control,
    which is the same trap `clear_rider_key` exists to avoid for `benefit_key`.

    **The 18-month lookback is NOT re-applied here.** It bounds what the picker
    OFFERS. Enforcing it as a validity rule would mean a claim became
    un-amendable the day its own anchor aged out — a broker correcting a figure
    two years later would be refused over a link nobody is touching. Same class
    as `recheck_coverage` / `recheck_period` in `claims.validate_claim_facts`:
    re-asserting a time-varying fact on an unrelated edit blocks corrections
    instead of catching anything.
    """
    if not anchor_id:
        return None
    mode = anchor_mode_for(product_code, sub_type, claim_kind=claim_kind)
    if mode == ANCHOR_SP_COURSE and visit_type != VISIT_FOLLOW_UP:
        mode = None
    if mode is None:
        return None
    if anchor_id == exclude_claim_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A claim can't be a follow-up to itself.",
        )
    claimant = _claimant(db, employee, dependant_id)
    anchor = db.get(Claim, anchor_id)
    # 404, never 403 — the same rule as every cross-tenant load. "Someone else's
    # claim" and "no such claim" must be indistinguishable.
    if anchor is None or anchor.employee_id not in claimant.employee_ids:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "That previous visit could not be found."
        )
    anchor = resolve_anchor_root(db, anchor)
    # Re-checked after following the link: the root is reached by an id STORED on
    # another row, so it is not covered by the check above. It should always be
    # the same person — nothing can store a cross-person anchor — which is
    # exactly why a root that isn't must not be trusted into a claim.
    if anchor.employee_id not in claimant.employee_ids:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "That previous visit could not be found."
        )
    if anchor.id == exclude_claim_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "A claim can't be a follow-up to itself.",
        )
    if not claimant.matches(anchor):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That visit was for a different person — pick a visit for the same "
            "patient as this claim.",
        )
    if anchor.status == CLAIM_STATUS_DRAFT or not _matches_mode(anchor, mode):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That visit can't be followed up by this claim type."
            if mode == ANCHOR_SP_COURSE
            else "Pick the hospital admission this consultation is claimed "
            "against.",
        )
    return anchor.id
