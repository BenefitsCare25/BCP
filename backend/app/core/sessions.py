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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

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

    `idle_minutes` (when set) enforces an inactivity window: a refresh token
    minted more than that many minutes ago is dead. Each rotation mints a fresh
    child, so the presented token's age since minting is the time since the last
    refresh — the client refreshes on access-token expiry, so this measures
    idleness at the access-token cadence. Does NOT commit — the caller does.
    """
    from app.models import AuthSession  # lazy

    now = datetime.now(UTC)
    row = db.execute(
        select(AuthSession).where(AuthSession.refresh_hash == hash_refresh(token))
    ).scalar_one_or_none()
    if row is None:
        return RotationResult(None, False)

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
            if issued + timedelta(minutes=idle_minutes) <= now:
                # Idle too long — kill this session (caller clears the cookie).
                row.revoked_at = now
                return RotationResult(None, False)

    row.rotated_at = now
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
    )
    return RotationResult(child, False)


def revoke_token(db: Session, token: str) -> None:
    """Logout: revoke the single session identified by this token. No commit."""
    from app.models import AuthSession  # lazy

    row = db.execute(
        select(AuthSession).where(AuthSession.refresh_hash == hash_refresh(token))
    ).scalar_one_or_none()
    if row is not None and row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
