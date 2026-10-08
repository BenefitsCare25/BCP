"""Subject-agnostic TOTP MFA — shared by HR users and portal members.

`subject_type` is "user" (HR admin) or "member" (portal employee). Secrets are
Fernet-encrypted at rest; recovery codes are hashed + single-use; replay is
guarded by `last_used_step`. All functions leave the commit to the caller.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuthMfa, AuthSession

logger = logging.getLogger(__name__)

# Starting enrolment binds a new second factor to the account, so a session
# whose sign-in is older than this must confirm the password first.
ENROLMENT_REAUTH_WINDOW = timedelta(minutes=10)


def _row(db: Session, subject_type: str, subject_id: str) -> AuthMfa | None:
    return db.execute(
        select(AuthMfa).where(
            AuthMfa.subject_type == subject_type, AuthMfa.subject_id == subject_id
        ).with_for_update()
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
    db: Session, subject_type: str, subject_id: str, account: str, issuer: str | None = None
) -> tuple[str, str]:
    """Create/replace an UNCONFIRMED secret. Returns (secret, otpauth_uri).
    `issuer` is the brand name the authenticator app shows for the account.
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
    return secret, provisioning_uri(secret, account, issuer)


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


def signed_in_recently(
    db: Session, session_id: str, now: datetime | None = None
) -> bool:
    """Whether the session's sign-in falls inside `ENROLMENT_REAUTH_WINDOW`.

    Measured from the family's ROOT row — the sign-in itself, where any second
    factor was verified. Refresh issues a new row every few minutes, so the
    live row's `issued_at` only says when the token was last rotated, not when
    the person last proved who they are.
    """
    current = db.get(AuthSession, session_id)
    if current is None:
        return False
    root = db.execute(select(AuthSession).where(
        AuthSession.family_id == current.family_id, AuthSession.parent_id.is_(None),
    )).scalar_one_or_none()
    if root is None:
        return False
    signed_in = root.issued_at
    if signed_in.tzinfo is None:
        signed_in = signed_in.replace(tzinfo=UTC)
    return signed_in > (now or datetime.now(UTC)) - ENROLMENT_REAUTH_WINDOW


def reauth_required() -> HTTPException:
    """403 for an enrolment start that needs the password confirmed first."""
    return HTTPException(status.HTTP_403_FORBIDDEN, {
        "code": "reauth_required",
        "message": "Confirm your password to set up two-factor.",
    })
