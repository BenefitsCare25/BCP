"""Deterministic (no-AI) claim checks — stage 1 of the review pipeline.

Each check emits ``{rule, status (pass|fail|warning), source: "deterministic",
evidence}``. A ``fail`` short-circuits the pipeline: the claim is flagged
without spending a single AI token.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import today as business_today
from app.models import Claim, Dependant, PolicyYear, StoredDocument
from app.models.claim import CLAIM_KIND_FLEX, LIVE_STATUSES
from app.models.stored_document import DOC_ENTITY_CLAIM, STORAGE_AVAILABLE
from app.schemas.api import BenefitStatementOut
from app.services.claim_fx import (
    FX_SOURCE_BROKER,
    FX_STATE_CONVERTED,
    FX_STATE_NOT_REQUIRED,
    fx_state,
    policy_amount,
)
from app.services.claim_intake import normalize_invoice_number
from app.services.claim_limits import (
    enforceable_policy_year_amount,
    item_setting,
    product_setting,
)
from app.services.fx import POLICY_CURRENCY


def _result(rule: str, status: str, evidence: str) -> dict[str, Any]:
    return {"rule": rule, "status": status, "source": "deterministic", "evidence": evidence}


def _flagging(rule: str, evidence: str) -> dict[str, Any]:
    """A warning that must still flag the verdict, without short-circuiting.

    The two existing severities only cover the ends: ``fail`` aborts the whole
    pipeline before a single document is read, and ``warning`` is advisory and
    cannot move the verdict on its own. Between them sits a real case — the
    claim is unfit to auto-clear for a reason that has nothing to do with its
    evidence, so the evidence should still be examined and the assessor should
    still be handed the claim.

    `verdict.compute_verdict` reads the ``flag`` key. Anything not setting it
    behaves exactly as before.
    """
    return {**_result(rule, "warning", evidence), "flag": True}


def _check_period(db: Session, claim: Claim) -> dict[str, Any]:
    rule = "Incurred date falls within the active policy year."
    year = db.get(PolicyYear, claim.policy_year_id)
    if year is None:
        return _result(rule, "fail", "Policy year not found.")
    if year.start_date <= claim.incurred_date <= year.end_date:
        return _result(
            rule, "pass",
            f"{claim.incurred_date.isoformat()} is within "
            f"{year.start_date.isoformat()} to {year.end_date.isoformat()}.",
        )
    return _result(
        rule, "fail",
        f"{claim.incurred_date.isoformat()} is outside the policy year "
        f"({year.start_date.isoformat()} to {year.end_date.isoformat()}).",
    )


def _check_has_documents(db: Session, claim: Claim) -> dict[str, Any]:
    rule = "The claim carries at least one document."
    count = db.execute(
        select(func.count())
        .select_from(StoredDocument)
        .where(
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id == claim.id,
            StoredDocument.storage_state == STORAGE_AVAILABLE,
        )
    ).scalar_one()
    if not count:
        return _result(rule, "fail", "The claim has no attached documents.")
    return _result(rule, "pass", f"{count} document(s) attached.")


def _check_duplicate_invoice(db: Session, claim: Claim) -> dict[str, Any]:
    """The invoice number — not the document hash — is what identifies the bill
    being claimed twice (see `services/claims.duplicate_invoice_claim_ids`).

    Two scopes, deliberately different: the member's OWN live claims are a
    duplicate and FAIL (submit blocks them, so one reaching here came in through
    a broker-entered case or a claim that went live concurrently); the same
    number on ANOTHER member's live claim is a WARNING — short receipt numbers
    collide across providers, so it is a signal for the broker to weigh, never
    an automatic flag on a member who did nothing wrong.
    """
    from app.services.claims import duplicate_invoice_claim_ids

    rule = "The invoice number is not already claimed."
    key = normalize_invoice_number(claim.invoice_number)
    if not key:
        return _result(rule, "pass", "No invoice number recorded on this claim.")

    own = duplicate_invoice_claim_ids(db, claim)
    if own:
        return _result(
            rule, "fail",
            f"Invoice {claim.invoice_number} is already on live claim(s): "
            f"{', '.join(own)}.",
        )

    rows = db.execute(
        select(Claim.id, Claim.invoice_number).where(
            Claim.client_id == claim.client_id,
            # Same benefit year only: an invoice has to be incurred in period,
            # so a match in another year cannot be the same bill — and it keeps
            # the scan bounded as the company's claim history grows.
            Claim.policy_year_id == claim.policy_year_id,
            Claim.employee_id != claim.employee_id,
            Claim.status.in_(LIVE_STATUSES),
        )
    ).all()
    others = sorted(
        cid for cid, number in rows if normalize_invoice_number(number) == key
    )
    if others:
        return _result(
            rule, "warning",
            f"Invoice {claim.invoice_number} also appears on another member's "
            f"live claim(s): {', '.join(others)}. Confirm these are different "
            "bills before approving.",
        )
    return _result(rule, "pass", f"Invoice {claim.invoice_number} is not claimed elsewhere.")


def _check_shared_documents(db: Session, claim: Claim) -> dict[str, Any] | None:
    """A document byte-identical to one on ANOTHER MEMBER's live claim.

    The hash test is no longer what decides a duplicate CLAIM (the invoice
    number is — see above), because within one member a shared document is the
    normal case: the multi-invoice intake flow attaches one episode's discharge
    summary to every claim of that episode. Across members it is nothing of the
    kind — two people cannot legitimately hold the same file — so the signal is
    kept here, scoped to where it means something, as a WARNING rather than the
    blanket `fail` it used to be. Returns None (no row at all) when clean: a
    mark that fires on nothing useful is better left unprinted.
    """
    hashes = db.execute(
        select(StoredDocument.sha256).where(
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id == claim.id,
            StoredDocument.storage_state == STORAGE_AVAILABLE,
        )
    ).scalars().all()
    if not hashes:
        return None
    others = db.execute(
        select(Claim.id)
        .join(StoredDocument, StoredDocument.entity_id == Claim.id)
        .where(
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.sha256.in_(hashes),
            StoredDocument.storage_state == STORAGE_AVAILABLE,
            Claim.client_id == claim.client_id,
            Claim.employee_id != claim.employee_id,
            Claim.status.in_(LIVE_STATUSES),
        )
        .distinct()
    ).scalars().all()
    if not others:
        return None
    return _result(
        "No document is shared with another member's live claim.",
        "warning",
        "A document here is byte-identical to one on another member's live "
        f"claim(s): {', '.join(sorted(others))}.",
    )


def _check_amount_vs_limit(claim: Claim, statement: BenefitStatementOut) -> dict[str, Any]:
    claimed = policy_amount(claim)
    if claim.claim_kind == CLAIM_KIND_FLEX:
        rule = "Claimed amount does not exceed the flex balance."
        flex = statement.flex
        balance = flex.flex_balance if flex is not None else None
        if balance is None:
            return _result(rule, "pass", "No flex balance recorded — not enforced.")
        if claimed is None:
            return _result(
                rule,
                "pass",
                "Policy-currency amount is awaiting conversion — not enforced.",
            )
        if claimed > float(balance):
            return _result(
                rule, "fail",
                f"Claimed {claimed:.2f} exceeds the remaining "
                f"flex balance of {float(balance):.2f}.",
            )
        return _result(
            rule, "pass",
            f"Claimed {claimed:.2f} within flex balance {float(balance):.2f}.",
        )

    rule = "Claimed amount does not exceed the verified annual policy limit."
    line = next(
        (c for c in statement.coverage if c.product_code == claim.product_code), None
    )
    schedule = line.benefit_schedule if line is not None else None
    candidates: list[float] = []
    if (
        overall_limit := enforceable_policy_year_amount(product_setting(schedule))
    ) is not None:
        candidates.append(overall_limit)
    wanted = (claim.benefit_key or "").strip().casefold()
    if wanted:
        for item in (schedule or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().casefold() != wanted:
                continue
            if (
                item_limit := enforceable_policy_year_amount(item_setting(item))
            ) is not None:
                candidates.append(item_limit)
            break
    if not candidates:
        return _result(rule, "pass", "No verified annual limit stated — not enforced.")
    if claimed is None:
        return _result(
            rule,
            "pass",
            "Policy-currency amount is awaiting conversion — not enforced.",
        )
    limit = min(candidates)
    if claimed > limit:
        # Warning, not fail: prior approved claims aren't netted here and the
        # broker may still partially approve.
        return _result(
            rule, "warning",
            f"Claimed {claimed:.2f} exceeds the verified annual policy "
            f"limit of {limit:.2f} for {claim.product_code}.",
        )
    return _result(
        rule, "pass",
        f"Claimed {claimed:.2f} within verified annual limit {limit:.2f}.",
    )


def _check_dependant_age(db: Session, claim: Claim) -> dict[str, Any] | None:
    """Any claim for a dependant (insured OR flex): warn when the dependant
    sits outside the scheme's eligibility age window (ANB convention, scheme
    meta overridable — the same window that sizes flex membership + coverage).
    Warning, not fail — insurer edge cases (student extensions, disabled
    dependants) are the broker's call."""
    if not claim.dependant_id:
        return None
    from app.services.flex_pricing_resolver import (
        _dependant_role_age,
        dependant_age_limits,
        get_pricing,
        role_age_eligible,
    )

    dep = db.get(Dependant, claim.dependant_id)
    year = db.get(PolicyYear, claim.policy_year_id)
    if dep is None or year is None:
        return None
    # Empty product id → scheme-level window (defaults overlaid with the flex
    # scheme's meta.dependant_age_limits) — same window that sizes coverage.
    limits = dependant_age_limits(get_pricing(db, claim.policy_year_id), "")
    prof = _dependant_role_age(dep, year.start_date)
    if prof is None:
        return None
    role, age = prof
    rule = "Claimed dependant is within the eligibility age window."
    if role_age_eligible(role, age, limits):
        return _result(
            rule, "pass",
            f"{role.capitalize()} dependant is within the {role} age window.",
        )
    win = limits.get(role) or {}
    return _result(
        rule, "warning",
        f"The claimed {role} is age {age} (ANB {age + 1 if age is not None else '?'}) "
        f"as of the policy year start — outside the eligibility window "
        f"({win.get('min', '—')} to {win.get('max', '—')} ANB). Confirm they "
        "are still covered before approving.",
    )


def _check_referral(claim: Claim) -> dict[str, Any] | None:
    """Specialist claims: surface a declared-N/A referral for broker attention
    (missing entirely is blocked at submit)."""
    from app.services.claim_intake import claim_profile_for

    if not claim_profile_for(claim.product_code).requires_referral:
        return None
    rule = "Specialist claim carries a referral letter."
    if claim.referral_document_id:
        return _result(rule, "pass", "A referral letter is attached to the claim.")
    return _result(
        rule, "warning",
        "The member declared the referral letter not applicable — confirm "
        "the specialist visit did not need one (e.g. A&E follow-up or "
        "direct-access specialty).",
    )


# How long a specialist referral is treated as valid. A CONVENTION, not a term
# we hold: Singapore group policies commonly accept a referral for 12 months,
# and no product configuration records it. So this only ever WARNS — it can
# never be the reason a claim is refused, and a broker who knows the insurer's
# actual rule can wave it through.
REFERRAL_VALIDITY_DAYS = 365


def _norm_text(value: str | None) -> str:
    """Compare clinical free text the way a person reads it, not byte-wise.

    Folds the "Other: " prefix the diagnosis picker adds to free text, so a
    catalog diagnosis on the admission and the same words typed on the consult
    are not reported as a mismatch.
    """
    text = (value or "").strip().lower()
    if text.startswith("other:"):
        text = text[len("other:") :].strip()
    return " ".join(text.split())


def _anchor_of(db: Session, claim: Claim) -> Claim | None:
    if not claim.related_claim_id:
        return None
    return db.get(Claim, claim.related_claim_id)


def _stay_dates(anchor: Claim) -> tuple[Any, Any]:
    """The admission's start and end. Falls back to the incurred date on both
    ends — a LOG case recorded from a guarantee request routinely carries no
    assessed dates, and a one-day window is the honest reading of what we know
    rather than an open-ended one."""
    start = anchor.admission_date or anchor.incurred_date
    end = anchor.discharge_date or anchor.admission_date or anchor.incurred_date
    return start, end


def _check_pre_post_window(db: Session, claim: Claim) -> dict[str, Any] | None:
    """A pre-/post-hospitalisation consult must fall within the policy's window
    either side of the stay it is claimed against.

    Runs only when there is an anchor AND the product states a window. NULL days
    mean NO RULE (`models/product_term.py`): most products will not carry these
    figures for a long time, and a claim must never be flagged because a broker
    has not transcribed a term.
    """
    from app.services.claim_episodes import anchor_mode_for_claim
    from app.services.claim_intake import ANCHOR_ADMISSION

    if anchor_mode_for_claim(claim) != ANCHOR_ADMISSION:
        return None
    anchor = _anchor_of(db, claim)
    if anchor is None:
        return None
    pre_days, post_days = _product_window_days(db, claim)
    if pre_days is None and post_days is None:
        return None
    rule = "Consultation falls within the pre-/post-hospitalisation window."
    start, end = _stay_dates(anchor)
    if pre_days is not None and claim.incurred_date < start - timedelta(days=pre_days):
        return _flagging(
            rule,
            f"The consultation on {claim.incurred_date.isoformat()} is more than "
            f"{pre_days} days before the admission on {start.isoformat()}, so it "
            "falls outside the pre-hospitalisation window for this product.",
        )
    if post_days is not None and claim.incurred_date > end + timedelta(days=post_days):
        return _flagging(
            rule,
            f"The consultation on {claim.incurred_date.isoformat()} is more than "
            f"{post_days} days after the discharge on {end.isoformat()}, so it "
            "falls outside the post-hospitalisation window for this product.",
        )
    return _result(
        rule, "pass",
        f"{claim.incurred_date.isoformat()} falls within the window around the "
        f"stay of {start.isoformat()} to {end.isoformat()}.",
    )


def _product_window_days(
    db: Session, claim: Claim
) -> tuple[int | None, int | None]:
    """The product's pre/post window, or (None, None) when it states none."""
    from app.core.deps import tenant_or_global
    from app.models import Product
    from app.models.product_term import ProductTerm

    if not claim.product_code:
        return None, None
    product_id = db.scalar(
        select(Product.id).where(
            tenant_or_global(Product.client_id, claim.client_id),
            func.upper(Product.code) == claim.product_code.strip().upper(),
        )
    )
    if product_id is None:
        return None, None
    term = db.execute(
        select(ProductTerm).where(
            ProductTerm.policy_year_id == claim.policy_year_id,
            ProductTerm.product_id == product_id,
        )
    ).scalar_one_or_none()
    if term is None:
        return None, None
    return term.pre_hosp_days, term.post_hosp_days


def _check_episode_link(db: Session, claim: Claim) -> dict[str, Any] | None:
    """A pre-/post-hospitalisation consult with no admission on record.

    The insurer pays this consult BECAUSE of an admission; a consult that names
    none cannot be matched to one, and today that is discovered by the insurer
    rather than by us. Flagged rather than failed — the admission may have been
    settled by Letter of Guarantee under a different broker, or predate the data
    we hold, and neither is the member's fault.
    """
    from app.services.claim_episodes import anchor_mode_for_claim
    from app.services.claim_intake import ANCHOR_ADMISSION

    if anchor_mode_for_claim(claim) != ANCHOR_ADMISSION:
        return None
    rule = "Pre-/post-hospitalisation consultation names the admission it follows."
    if claim.related_claim_id:
        return _result(rule, "pass", "The claim names the hospital stay it follows.")
    return _flagging(
        rule,
        "This consultation is claimed as pre- or post-hospitalisation but names "
        "no admission. Match it to the hospital stay before sending it on, or "
        "reclassify it as an outpatient specialist claim.",
    )


def _check_episode_diagnosis(db: Session, claim: Claim) -> dict[str, Any] | None:
    """The follow-up should be for the condition the anchor visit was for.

    An insurer rejects a pre/post consult — and a specialist follow-up — for an
    unrelated condition, so a mismatch here is the difference between a paid
    claim and one that comes back weeks later.
    """
    anchor = _anchor_of(db, claim)
    if anchor is None:
        return None
    mine, theirs = _norm_text(claim.diagnosis), _norm_text(anchor.diagnosis)
    if not mine or not theirs:
        return None
    rule = "Diagnosis matches the visit this claim follows."
    if mine == theirs:
        return _result(rule, "pass", f"Both visits state {claim.diagnosis!r}.")
    return _flagging(
        rule,
        f"This claim states {claim.diagnosis!r} but the visit it follows states "
        f"{anchor.diagnosis!r}. A follow-up for a different condition is not "
        "claimable against the earlier visit.",
    )


def _check_episode_doctor(db: Session, claim: Claim) -> dict[str, Any] | None:
    """The consult's doctor against the anchor's.

    Advisory only, and deliberately so: a different consultant in the same team
    routinely sees the patient at the follow-up, so this is context for an
    assessor rather than a finding. It also seldom runs — an admission claim has
    no reason to name a doctor, since only pre/post consults are asked for one.
    """
    anchor = _anchor_of(db, claim)
    if anchor is None:
        return None
    mine, theirs = _norm_text(claim.doctor_name), _norm_text(anchor.doctor_name)
    if not mine or not theirs or mine == theirs:
        return None
    return _result(
        "Treating doctor matches the visit this claim follows.",
        "warning",
        f"This claim names {claim.doctor_name!r}; the visit it follows names "
        f"{anchor.doctor_name!r}. Confirm this is the same course of treatment.",
    )


def _check_referral_age(db: Session, claim: Claim) -> dict[str, Any] | None:
    """A specialist visit riding a referral letter older than its validity.

    Only runs when the letter carries its OWN issue date
    (`stored_documents.issued_on`). The upload date is deliberately not used as
    a stand-in: a member routinely scans a months-old letter the day they first
    claim, and reading that as the issue date would date every letter to the
    first claim it was used on.
    """
    if not claim.referral_document_id:
        return None
    doc = db.get(StoredDocument, claim.referral_document_id)
    if (
        doc is None
        or doc.storage_state != STORAGE_AVAILABLE
        or doc.issued_on is None
    ):
        return None
    rule = "Referral letter is still valid at the date of the visit."
    age = (claim.incurred_date - doc.issued_on).days
    if age < 0:
        # A letter that postdates the visit did not authorise it. Reading the
        # negative age as "well within validity" made the strongest signal here
        # the quietest one — it passed, printing `max(age, 0)` as "0 days
        # before the visit", which is not a thing that happened.
        return _flagging(
            rule,
            f"The referral letter is dated {doc.issued_on.isoformat()}, "
            f"{-age} days AFTER the visit on "
            f"{claim.incurred_date.isoformat()}. A referral is written before "
            "the consultation it authorises — check the letter belongs to this "
            "visit and that neither date was mis-keyed.",
        )
    if age <= REFERRAL_VALIDITY_DAYS:
        return _result(
            rule, "pass",
            f"The referral was issued on {doc.issued_on.isoformat()}, "
            f"{age} days before the visit.",
        )
    return _flagging(
        rule,
        f"The referral letter was issued on {doc.issued_on.isoformat()}, "
        f"{age} days before this visit. Most policies treat a referral as valid "
        f"for {REFERRAL_VALIDITY_DAYS // 30} months — confirm the insurer "
        "accepts it or ask the member for a current referral.",
    )


def _check_future_date(claim: Claim) -> dict[str, Any] | None:
    # Business date, not the UTC one — the same clock submit and the served
    # claim window use (`core/clock.py`). On UTC, every day between midnight and
    # 8am Singapore, a member's same-morning treatment passed the form and the
    # submit bound and then landed here flagged "after today", sending a
    # perfectly ordinary claim to a broker for no reason.
    if claim.incurred_date <= business_today():
        return None
    return _result(
        "Incurred date is not in the future.",
        "warning",
        f"Incurred date {claim.incurred_date.isoformat()} is after today — "
        "verify the treatment actually took place.",
    )


def _check_currency(claim: Claim) -> dict[str, Any]:
    """Whether this claim has a policy-currency value at all.

    A foreign claim is fine; a foreign claim NOBODY CAN PRICE is not. The second
    cannot be compared to a limit, cannot be summed into a bucket, and cannot be
    approved — so it must reach a person, and this is what puts it in front of
    one.

    ``flag`` rather than ``fail``: a hard fail short-circuits the whole pipeline
    (see the module docstring), so a currency API being unreachable would cost
    the assessor the entire document comparison on a claim whose paperwork is
    very probably fine. The conversion and the evidence are unrelated questions;
    this flags the verdict without cancelling the answer to the other one.
    """
    rule = f"Claim has a value in the policy currency ({POLICY_CURRENCY})."
    state = fx_state(claim)
    if state == FX_STATE_NOT_REQUIRED:
        return _result(rule, "pass", f"Claim is already in {POLICY_CURRENCY}.")

    code = (claim.currency or "").upper()
    if state == FX_STATE_CONVERTED:
        detail = (
            f"{code} {claim.amount_claimed:,.2f} = {POLICY_CURRENCY} "
            f"{claim.amount_converted:,.2f}"
        )
        if claim.fx_source == FX_SOURCE_BROKER:
            return _result(rule, "pass", f"{detail}, entered by an assessor.")
        rate = f" at {claim.fx_rate:,.6g}" if claim.fx_rate else ""
        if claim.fx_rate_date and claim.fx_rate_date != claim.incurred_date:
            # Expected, not suspicious — there is no published rate for a
            # weekend or a holiday. Said plainly so an assessor comparing the
            # two dates doesn't read a normal Saturday receipt as a discrepancy.
            when = (
                f" using the rate published {claim.fx_rate_date.isoformat()} "
                f"(none is published for {claim.incurred_date.isoformat()})"
            )
        else:
            when = f" at the {claim.fx_rate_date.isoformat()} rate" if claim.fx_rate_date else ""
        return _result(rule, "pass", f"{detail}{rate}{when}.")

    return _flagging(
        rule,
        f"No exchange rate could be obtained for {code} on "
        f"{claim.incurred_date.isoformat()}, so this claim has no "
        f"{POLICY_CURRENCY} value. It cannot be checked against the member's "
        f"remaining limit, and approving it needs the {POLICY_CURRENCY} amount "
        "entered by hand.",
    )


def deterministic_rule_results(
    db: Session, claim: Claim, statement: BenefitStatementOut
) -> list[dict[str, Any]]:
    results = [
        _check_period(db, claim),
        _check_has_documents(db, claim),
        _check_duplicate_invoice(db, claim),
        _check_amount_vs_limit(claim, statement),
        _check_currency(claim),
    ]
    for extra in (
        _check_shared_documents(db, claim),
        _check_referral(claim),
        _check_referral_age(db, claim),
        _check_future_date(claim),
        _check_dependant_age(db, claim),
        # Episode checks (`services/claim_episodes.py`) — every one of them
        # returns None when the claim continues nothing, which is most claims.
        _check_episode_link(db, claim),
        _check_pre_post_window(db, claim),
        _check_episode_diagnosis(db, claim),
        _check_episode_doctor(db, claim),
    ):
        if extra is not None:
            results.append(extra)
    return results


def has_failures(rule_results: list[dict[str, Any]]) -> bool:
    return any(r.get("status") == "fail" for r in rule_results)
