"""Subject-agnostic TOTP MFA — shared by HR users and portal members.

`subject_type` is "user" (HR admin) or "member" (portal employee). Secrets are
Fernet-encrypted at rest; recovery codes are hashed + single-use; replay is
guarded by `last_used_step`. All functions leave the commit to the caller.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _row(db: Session, subject_type: str, subject_id: str):
    from app.models import AuthMfa

    return db.execute(
        select(AuthMfa).where(
            AuthMfa.subject_type == subject_type, AuthMfa.subject_id == subject_id
        )
    ).scalar_one_or_none()


def status_for(db: Session, subject_type: str, subject_id: str) -> str:
    """"confirmed" | "pending" | "none"."""
    row = _row(db, subject_type, subject_id)
    if row is None:
        return "none"
    return "confirmed" if row.confirmed_at is not None else "pending"


def has_confirmed(db: Session, subject_type: str, subject_id: str) -> bool:
    return status_for(db, subject_type, subject_id) == "confirmed"


def verify_totp(db: Session, subject_type: str, subject_id: str, code: str) -> bool:
    """Verify a TOTP code, enforcing the replay guard. Caller commits."""
    from app.core.crypto import decrypt_secret
    from app.core.totp import verify_totp as _verify

    row = _row(db, subject_type, subject_id)
    if row is None:
        return False
    try:
        secret = decrypt_secret(row.totp_secret_enc.encode())
    except Exception:  # pragma: no cover - corrupt/rotated secret
        logger.warning("Undecryptable TOTP secret for %s %s", subject_type, subject_id)
        return False
    step = _verify(secret, code, after_step=row.last_used_step)
    if step is None:
        return False
    row.last_used_step = step
    if row.confirmed_at is None:
        row.confirmed_at = datetime.now(UTC)
    return True


def consume_recovery_code(
    db: Session, subject_type: str, subject_id: str, code: str
) -> bool:
    """Single-use recovery code: match a stored hash, remove it. Caller commits."""
    from app.core.totp import hash_recovery_code

    row = _row(db, subject_type, subject_id)
    if row is None or not row.recovery_codes:
        return False
    target = hash_recovery_code(code)
    codes = list(row.recovery_codes)
    if target not in codes:
        return False
    codes.remove(target)
    row.recovery_codes = codes  # reassign so the JSON column is flagged dirty
    return True


def start_enrollment(
    db: Session, subject_type: str, subject_id: str, account: str
) -> tuple[str, str]:
    """Create/replace an UNCONFIRMED secret. Returns (secret, otpauth_uri).
    409 if already confirmed (disable first). Caller commits."""
    from app.core.crypto import encrypt_secret
    from app.core.totp import generate_secret, provisioning_uri
    from app.models import AuthMfa

    row = _row(db, subject_type, subject_id)
    if row is not None and row.confirmed_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Two-factor authentication is already set up. Disable it first to re-enrol.",
        )
    secret = generate_secret()
    enc = encrypt_secret(secret).decode()
    if row is None:
        row = AuthMfa(
            subject_type=subject_type, subject_id=subject_id, totp_secret_enc=enc
        )
        db.add(row)
    else:
        row.totp_secret_enc = enc
        row.last_used_step = None
        row.recovery_codes = None
    return secret, provisioning_uri(secret, account)


def confirm_enrollment(
    db: Session, subject_type: str, subject_id: str, code: str
) -> list[str] | None:
    """Confirm a pending enrolment with the first code. Returns fresh recovery
    codes (shown once) on success, else None. Caller commits."""
    from app.core.totp import generate_recovery_codes, hash_recovery_code

    row = _row(db, subject_type, subject_id)
    if row is None or row.confirmed_at is not None:
        return None
    if not verify_totp(db, subject_type, subject_id, code):
        return None
    recovery = generate_recovery_codes()
    row.recovery_codes = [hash_recovery_code(c) for c in recovery]
    return recovery


def disable(db: Session, subject_type: str, subject_id: str) -> None:
    """Remove an enrolment. Caller commits."""
    row = _row(db, subject_type, subject_id)
    if row is not None:
        db.delete(row)
