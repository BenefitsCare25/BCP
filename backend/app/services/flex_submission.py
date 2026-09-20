"""Opt-in Flex submission rule, distinct from reimbursement/approval limits."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.tenancy import is_postgres
from app.models import Claim, Employee, FlexScheme
from app.models.claim import (
    CLAIM_KIND_FLEX,
    CLAIM_STATUS_DRAFT,
    CLAIM_STATUS_PAID,
    LIVE_STATUSES,
    PENDING_STATUSES,
    SETTLED_STATUSES,
)
from app.services.claim_fx import policy_amount
from app.services.member_statement import build_member_statement

SUBMISSION_BASES = frozenset({"off", "paid", "approved", "reserved"})


def submission_rule_errors(scheme: dict[str, Any]) -> list[str]:
    meta = scheme.get("meta")
    value = meta.get("claim_submission_basis", "off") if isinstance(meta, dict) else "off"
    if not isinstance(value, str) or value not in SUBMISSION_BASES:
        return ["Flex claim submission basis must be off, paid, approved or reserved."]
    return []


def lock_flex_wallet(db: Session, claim: Claim) -> None:
    """Serialize all categories sharing a wallet, including simultaneous submissions."""
    if is_postgres(db):
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"flex-wallet|{claim.client_id}|{claim.policy_year_id}|{claim.employee_id}"},
        )


def assert_flex_submission_allowed(
    db: Session,
    claim: Claim,
    employee: Employee,
    *,
    new_case: bool = False,
    eligibility_check: Callable[[Session, Claim, Employee, dict[str, Any]], None] | None = None,
) -> None:
    # Replies and corrections to an existing case must remain possible.
    if claim.claim_kind != CLAIM_KIND_FLEX or (claim.status != CLAIM_STATUS_DRAFT and not new_case):
        return
    scheme = db.scalar(select(FlexScheme).where(FlexScheme.policy_year_id == claim.policy_year_id))
    bag = scheme.scheme if scheme and isinstance(scheme.scheme, dict) else {}
    meta = bag.get("meta") or {}
    if not isinstance(meta, dict):
        raise HTTPException(409, "The Flex submission setting needs administrator review.")
    if eligibility_check is not None:
        eligibility_check(db, claim, employee, meta)
    basis = meta.get("claim_submission_basis", "off")
    if basis == "off":
        return
    if not isinstance(basis, str) or basis not in SUBMISSION_BASES:
        raise HTTPException(409, "The Flex submission setting needs administrator review.")
    lock_flex_wallet(db, claim)
    # Read after acquiring the wallet lock, not from a cached browser balance.
    flex = build_member_statement(db, employee).flex
    if flex is None:
        return  # The ordinary coverage validator reports missing eligibility.
    allowance = flex.flex_balance if flex.flex_balance is not None else flex.wallet_amount
    if allowance is None:
        raise HTTPException(
            409, "Your Flex balance is unavailable. Please contact your benefits team."
        )
    used = Decimal("0")
    rows = db.scalars(
        select(Claim)
        .where(
            Claim.client_id == claim.client_id,
            Claim.policy_year_id == claim.policy_year_id,
            Claim.employee_id == employee.id,
            Claim.claim_kind == CLAIM_KIND_FLEX,
            Claim.status.in_(LIVE_STATUSES),
            Claim.id != claim.id,
        )
        .execution_options(populate_existing=True)
    ).all()
    for other in rows:
        amount: float | Decimal | None = None
        if basis == "paid" and other.status == CLAIM_STATUS_PAID:
            # Actual payments can differ from approvals. Never infer payment from approval.
            amount = other.payment_amount
            if amount is None:
                raise HTTPException(
                    409, "A Flex payment needs reconciliation. Contact your benefits team."
                )
        elif basis in {"approved", "reserved"} and other.status in SETTLED_STATUSES:
            amount = other.amount_approved
        elif basis == "reserved" and other.status in PENDING_STATUSES:
            amount = policy_amount(other)
            if amount is None:
                raise HTTPException(
                    409,
                    "A pending Flex claim needs currency conversion "
                    "before another can be submitted.",
                )
        if amount is not None:
            used += Decimal(str(amount))
    if Decimal(str(allowance)) - used <= 0:
        raise HTTPException(
            422,
            detail={
                "code": "flex_wallet_exhausted",
                "message": (
                    "Your Flex wallet is fully used. You can still respond to existing claims."
                ),
                "basis": basis,
            },
        )
