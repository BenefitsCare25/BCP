"""Shared credential-login primitives for any password-backed principal
(HR users + portal members): per-identifier lockout with exponential backoff,
and the single-use set-password version stamp.

These operate structurally on any object exposing `failed_attempts`,
`locked_until`, and `password_updated_at` (AuthCredential, MemberAccount).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

_LOCK_THRESHOLD = 5
_LOCK_BASE_SECONDS = 60
_LOCK_CAP_SECONDS = 3600


def is_locked(cred, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    locked = cred.locked_until
    if locked is None:
        return False
    if locked.tzinfo is None:
        locked = locked.replace(tzinfo=UTC)
    return locked > now


def register_failure(cred) -> None:
    """Increment the failure counter and apply exponential backoff past the
    threshold. Caller commits."""
    cred.failed_attempts = (cred.failed_attempts or 0) + 1
    if cred.failed_attempts >= _LOCK_THRESHOLD:
        over = cred.failed_attempts - _LOCK_THRESHOLD
        delay = min(_LOCK_BASE_SECONDS * (2**over), _LOCK_CAP_SECONDS)
        cred.locked_until = datetime.now(UTC) + timedelta(seconds=delay)


def reset_failures(cred) -> None:
    cred.failed_attempts = 0
    cred.locked_until = None


def credential_version(cred) -> int:
    """Monotonic stamp that makes set-password tokens single-use: the password's
    last-update time in MICROSECONDS (second granularity would collide when a
    set-password lands in the same second as provisioning), 0 if never set."""
    ts = getattr(cred, "password_updated_at", None)
    if ts is None:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000)
