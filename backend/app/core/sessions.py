"""Rotating refresh-token sessions with reuse detection.

The refresh token is an opaque 256-bit secret; only its SHA-256 hash is stored
(`auth_sessions.refresh_hash`). Each rotation issues a child in the same
`family_id` and marks the parent `rotated_at`. Presenting a token whose row is
already rotated or revoked means the token was replayed (stolen) — the whole
family is revoked. Surface-agnostic: `subject_type` is "user" (HR) or "member".
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models import AuthSession

_REFRESH_BYTES = 32


def _new_token() -> str:
    return secrets.token_urlsafe(_REFRESH_BYTES)


def hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class IssuedSession:
    token: str  # raw refresh token — set as a cookie, never persisted raw
    session_id: str
    family_id: str
    expires_at: datetime


def issue_session(
    db: Session,
    *,
    subject_type: str,
    subject_id: str,
    client_id: str | None,
    broker_firm_id: str | None,
    absolute_hours: int,
    family_id: str | None = None,
    parent_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    subdomain: str | None = None,
    expires_at: datetime | None = None,
    mfa_verified: bool = False,
) -> IssuedSession:
    """Create a session row and return the raw token. Does NOT commit.

    `expires_at` pins the absolute expiry; when omitted it's computed from
    `absolute_hours`. Rotation passes the family's ORIGINAL expiry so the
    absolute lifetime is anchored at first login and does not slide forward on
    each refresh.
    """
    from app.models import AuthSession  # lazy

    token = _new_token()
    now = datetime.now(UTC)
    if expires_at is None:
        expires_at = now + timedelta(hours=absolute_hours)
    row = AuthSession(
        subject_type=subject_type,
        subject_id=subject_id,
        client_id=client_id,
        broker_firm_id=broker_firm_id,
        family_id=family_id or secrets.token_urlsafe(16),
        refresh_hash=hash_refresh(token),
        parent_id=parent_id,
        issued_at=now,
        expires_at=expires_at,
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
        subdomain=subdomain,
        mfa_verified=mfa_verified,
        last_seen_at=now,
    )
    db.add(row)
    db.flush()  # populate row.id / family_id for the return value
    return IssuedSession(
        token=token, session_id=row.id, family_id=row.family_id, expires_at=expires_at
    )


def revoke_family(db: Session, family_id: str) -> None:
    """Revoke every live session in a family (reuse-detection response)."""
    from app.models import AuthSession  # lazy

    db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


def revoke_all_for_subject(db: Session, subject_type: str, subject_id: str) -> int:
    """Revoke every live session a subject holds. Returns the count. No commit.

    Called whenever a password changes. Without it a reset gave no containment:
    an attacker who had already signed in kept rotating their refresh family for
    the full absolute lifetime, so the victim's "change my password" did not
    actually evict them.
    """
    from app.models import AuthSession  # lazy

    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.subject_type == subject_type,
            AuthSession.subject_id == subject_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    return int(getattr(result, "rowcount", 0) or 0)


@dataclass(frozen=True)
class RotationResult:
    session: IssuedSession | None
    reuse_detected: bool


def rotate_session(
    db: Session,
    token: str,
    *,
    absolute_hours: int,
    idle_minutes: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    subdomain: str | None = None,
) -> RotationResult:
    """Validate a refresh token and issue its successor.

    - Unknown / expired / idle-timed-out token → (None, reuse=False).
    - Already-rotated or revoked token → REUSE: revoke the family, (None, True).
    - Valid, live token → mark it rotated, issue a child, (child, False).

    `idle_minutes` (when set) bounds inactivity since the last authenticated
    request or refresh. Rotation never extends the absolute expiry.
    Does NOT commit - the caller does.
    """
    from app.models import AuthSession  # lazy

    now = datetime.now(UTC)
    row = db.execute(
        select(AuthSession).where(AuthSession.refresh_hash == hash_refresh(token)).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        return RotationResult(None, False)

    root = db.execute(select(AuthSession).where(
        AuthSession.family_id == row.family_id, AuthSession.parent_id.is_(None),
    )).scalar_one_or_none()
    if root is None or root.revoked_at is not None:
        revoke_family(db, row.family_id)
        return RotationResult(None, True)

    expires = row.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)

    if row.revoked_at is not None or row.rotated_at is not None:
        # Replay of a consumed token — the family is compromised.
        revoke_family(db, row.family_id)
        return RotationResult(None, True)

    if expires is not None and expires <= now:
        return RotationResult(None, False)

    if idle_minutes and idle_minutes > 0:
        issued = row.issued_at
        if issued is not None:
            if issued.tzinfo is None:
                issued = issued.replace(tzinfo=UTC)
            seen = row.last_seen_at or issued
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=UTC)
            if seen + timedelta(minutes=idle_minutes) <= now:
                # Idle too long — kill this session (caller clears the cookie).
                row.revoked_at = now
                return RotationResult(None, False)

    claimed = db.execute(update(AuthSession).where(
        AuthSession.id == row.id, AuthSession.rotated_at.is_(None),
        AuthSession.revoked_at.is_(None),
    ).values(rotated_at=now))
    if getattr(claimed, "rowcount", 0) != 1:
        revoke_family(db, row.family_id)
        return RotationResult(None, True)
    child = issue_session(
        db,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        client_id=row.client_id,
        broker_firm_id=row.broker_firm_id,
        absolute_hours=absolute_hours,
        # Carry the family's original absolute expiry forward so the cap is
        # fixed at first login rather than resetting on every refresh.
        expires_at=expires,
        family_id=row.family_id,
        parent_id=row.id,
        ip=ip,
        user_agent=user_agent,
        subdomain=subdomain,
        mfa_verified=row.mfa_verified,
    )
    return RotationResult(child, False)


def revoke_token(db: Session, token: str) -> AuthSession | None:
    """Logout: revoke the token's entire session family. No commit.

    Returns the row it revoked, else None (unknown token, or one already
    revoked). The caller needs it to attribute the logout audit event: an
    auth event with no subject is unattributable and therefore useless, and a
    repeat POST with a spent cookie is not a second sign-out to record.
    """
    from app.models import AuthSession  # lazy

    row = db.execute(
        select(AuthSession).where(AuthSession.refresh_hash == hash_refresh(token))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        revoke_family(db, row.family_id)
        return row
    return None


def validate_access_session(
    db: Session, session_id: str, *, subject_type: str, subject_id: str,
    client_id: str, idle_minutes: int,
) -> AuthSession:
    """Reject revoked families and enforce inactivity on access-token requests."""
    from app.models import AuthSession

    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended. Sign in again.")
    original = db.get(AuthSession, session_id)
    if (
        original is None or original.subject_type != subject_type
        or original.subject_id != subject_id or original.client_id != client_id
        or original.revoked_at is not None
    ):
        raise invalid
    # A logout racing a rotation must also invalidate a newly inserted child.
    root = db.execute(select(AuthSession).where(
        AuthSession.family_id == original.family_id, AuthSession.parent_id.is_(None),
    )).scalar_one_or_none()
    if root is None or root.revoked_at is not None:
        raise invalid
    live = db.execute(select(AuthSession).where(
        AuthSession.family_id == original.family_id,
        AuthSession.revoked_at.is_(None), AuthSession.rotated_at.is_(None),
    ).order_by(AuthSession.issued_at.desc())).scalars().first()
    if live is None:
        raise invalid
    now = datetime.now(UTC)
    expiry = live.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    seen = live.last_seen_at or live.issued_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    if expiry <= now or seen + timedelta(minutes=idle_minutes) <= now:
        revoke_family(db, live.family_id)
        db.commit()
        raise invalid
    if seen + timedelta(seconds=30) <= now:
        live.last_seen_at = now
        db.commit()
    return live


def confirm_session_mfa(db: Session, session_id: str) -> None:
    from app.models import AuthSession

    row = db.get(AuthSession, session_id)
    if row is not None:
        db.execute(update(AuthSession).where(
            AuthSession.family_id == row.family_id, AuthSession.revoked_at.is_(None),
        ).values(mfa_verified=True))
