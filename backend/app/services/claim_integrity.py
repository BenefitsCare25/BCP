"""Concurrency and idempotency controls shared by claim command surfaces."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.tenancy import is_postgres
from app.models import Claim, ClaimCommand
from app.services.claim_intake import normalize_invoice_number


def lock_duplicate_invoice_scope(db: Session, claim: Claim) -> None:
    """Serialize member submissions carrying the same normalized invoice."""
    if not is_postgres(db):
        return
    invoice = normalize_invoice_number(claim.invoice_number)
    if not invoice:
        return
    scope = f"{claim.client_id}|{claim.employee_id}|{invoice}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {"scope": scope},
    )


def lock_claim_command_key(
    db: Session, client_id: str, idempotency_key: str | None
) -> None:
    """Serialize first use of a portal idempotency key on PostgreSQL."""
    if not idempotency_key or not is_postgres(db):
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"claim-command|{client_id}|{idempotency_key}"},
    )


def lock_claim_form_draft_scope(
    db: Session, employee_id: str, policy_year_id: str
) -> None:
    """Serialize create/update of the member's single form working copy."""
    if not is_postgres(db):
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"claim-form-draft|{employee_id}|{policy_year_id}"},
    )


def _key_reused() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "idempotency_key_reused",
            "message": "This Idempotency-Key was used for another command.",
        },
    )


def replayed_claim_for_command(
    db: Session,
    *,
    client_id: str,
    employee_id: str,
    action: str,
    idempotency_key: str | None,
    request_hash: str | None = None,
) -> Claim | None:
    """Return this member's claim produced by an earlier portal command."""
    if not idempotency_key:
        return None
    command = db.execute(
        select(ClaimCommand).where(
            ClaimCommand.client_id == client_id,
            ClaimCommand.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if command is None:
        return None
    if command.action != action or (
        request_hash is not None and command.request_hash != request_hash
    ):
        raise _key_reused()
    claim = db.get(Claim, command.claim_id)
    if claim is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_result_missing",
                "message": "The earlier claim request can no longer be replayed.",
            },
        )
    if claim.employee_id != employee_id:
        raise _key_reused()
    return claim


def is_replayed_claim_command(
    db: Session,
    claim: Claim,
    action: str,
    idempotency_key: str | None,
    request_hash: str | None = None,
) -> bool:
    """Return true for a completed retry; reject cross-command key reuse."""
    if not idempotency_key:
        return False
    existing = db.execute(
        select(ClaimCommand).where(
            ClaimCommand.client_id == claim.client_id,
            ClaimCommand.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is None:
        return False
    if (
        existing.claim_id != claim.id
        or existing.action != action
        or (request_hash is not None and existing.request_hash != request_hash)
    ):
        raise _key_reused()
    return True


def record_claim_command(
    db: Session,
    claim: Claim,
    action: str,
    idempotency_key: str | None,
    request_hash: str | None = None,
) -> None:
    if not idempotency_key:
        return
    command = ClaimCommand(
        client_id=claim.client_id,
        claim_id=claim.id,
        action=action,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    db.add(command)
    try:
        db.flush((command,))
    except IntegrityError:
        db.rollback()
        raise _key_reused() from None
