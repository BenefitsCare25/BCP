"""Deterministic (no-AI) claim checks — stage 1 of the review pipeline.

Each check emits ``{rule, status (pass|fail|warning), source: "deterministic",
evidence}``. A ``fail`` short-circuits the pipeline: the claim is flagged
without spending a single AI token.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Claim, PolicyYear, StoredDocument
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
    return [
        _check_period(db, claim),
        _check_duplicate_receipts(db, claim),
        _check_amount_vs_limit(claim, statement),
        _check_currency(claim),
    ]


def has_failures(rule_results: list[dict[str, Any]]) -> bool:
    return any(r.get("status") == "fail" for r in rule_results)
