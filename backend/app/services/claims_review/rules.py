"""Deterministic (no-AI) claim checks — stage 1 of the review pipeline.

Each check emits ``{rule, status (pass|fail|warning), source: "deterministic",
evidence}``. A ``fail`` short-circuits the pipeline: the claim is flagged
without spending a single AI token.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, Dependant, PolicyYear, StoredDocument
from app.models.claim import CLAIM_KIND_FLEX, LIVE_STATUSES
from app.models.stored_document import DOC_ENTITY_CLAIM
from app.schemas.api import BenefitStatementOut
from app.services.utilization import parse_limit_amount


def _result(rule: str, status: str, evidence: str) -> dict[str, Any]:
    return {"rule": rule, "status": status, "source": "deterministic", "evidence": evidence}


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


def _check_duplicate_receipts(db: Session, claim: Claim) -> dict[str, Any]:
    rule = "No receipt is reused from another live claim."
    docs = db.execute(
        select(StoredDocument).where(
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id == claim.id,
        )
    ).scalars().all()
    if not docs:
        return _result(rule, "fail", "The claim has no attached documents.")
    dupes = db.execute(
        select(StoredDocument).where(
            StoredDocument.client_id == claim.client_id,
            StoredDocument.entity_type == DOC_ENTITY_CLAIM,
            StoredDocument.entity_id != claim.id,
            StoredDocument.sha256.in_([d.sha256 for d in docs]),
        )
    ).scalars().all()
    if dupes:
        live = db.execute(
            select(Claim.id).where(
                Claim.id.in_({d.entity_id for d in dupes}),
                Claim.status.in_(LIVE_STATUSES),
            )
        ).scalars().all()
        if live:
            return _result(
                rule, "fail",
                "A receipt with an identical SHA-256 is attached to live "
                f"claim(s): {', '.join(sorted(live))}.",
            )
    return _result(rule, "pass", f"{len(docs)} document(s), no hash reuse across live claims.")


def _check_amount_vs_limit(claim: Claim, statement: BenefitStatementOut) -> dict[str, Any]:
    if claim.claim_kind == CLAIM_KIND_FLEX:
        rule = "Claimed amount does not exceed the flex balance."
        flex = statement.flex
        balance = flex.flex_balance if flex is not None else None
        if balance is None:
            return _result(rule, "pass", "No flex balance recorded — not enforced.")
        if claim.amount_claimed > float(balance):
            return _result(
                rule, "fail",
                f"Claimed {claim.amount_claimed:.2f} exceeds the remaining "
                f"flex balance of {float(balance):.2f}.",
            )
        return _result(
            rule, "pass",
            f"Claimed {claim.amount_claimed:.2f} within flex balance {float(balance):.2f}.",
        )

    rule = "Claimed amount does not exceed the annual policy limit."
    line = next(
        (c for c in statement.coverage if c.product_code == claim.product_code), None
    )
    limit = parse_limit_amount(line.annual_policy_limit if line else None)
    if limit is None:
        return _result(rule, "pass", "No numeric annual limit stated — not enforced.")
    if claim.amount_claimed > limit:
        # Warning, not fail: prior approved claims aren't netted here and the
        # broker may still partially approve.
        return _result(
            rule, "warning",
            f"Claimed {claim.amount_claimed:.2f} exceeds the annual policy "
            f"limit of {limit:.2f} for {claim.product_code}.",
        )
    return _result(
        rule, "pass",
        f"Claimed {claim.amount_claimed:.2f} within annual limit {limit:.2f}.",
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


def _check_future_date(claim: Claim) -> dict[str, Any] | None:
    from datetime import date as _date

    if claim.incurred_date <= _date.today():
        return None
    return _result(
        "Incurred date is not in the future.",
        "warning",
        f"Incurred date {claim.incurred_date.isoformat()} is after today — "
        "verify the treatment actually took place.",
    )


def _check_currency(claim: Claim) -> dict[str, Any]:
    rule = "Claim is in the policy currency (SGD)."
    if (claim.currency or "SGD").upper() == "SGD":
        return _result(rule, "pass", "Claim currency is SGD.")
    return _result(
        rule, "warning",
        f"Claim is in {claim.currency.upper()} — conversion to SGD needs "
        "broker confirmation.",
    )


def deterministic_rule_results(
    db: Session, claim: Claim, statement: BenefitStatementOut
) -> list[dict[str, Any]]:
    results = [
        _check_period(db, claim),
        _check_duplicate_receipts(db, claim),
        _check_amount_vs_limit(claim, statement),
        _check_currency(claim),
    ]
    for extra in (
        _check_referral(claim),
        _check_future_date(claim),
        _check_dependant_age(db, claim),
    ):
        if extra is not None:
            results.append(extra)
    return results


def has_failures(rule_results: list[dict[str, Any]]) -> bool:
    return any(r.get("status") == "fail" for r in rule_results)
