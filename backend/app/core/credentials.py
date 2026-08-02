"""Shared credential-login primitives for any password-backed principal
(HR users + portal members): per-identifier lockout with exponential backoff,
and the single-use set-password version stamp.

These operate structurally on any object exposing `failed_attempts`,
`locked_until`, and `password_updated_at` (AuthCredential, MemberAccount).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

_LOCK_THRESHOLD = 5
_LOCK_BASE_SECONDS = 60
_LOCK_CAP_SECONDS = 3600


class LockableCredential(Protocol):
    """Structural shape shared by AuthCredential and MemberAccount — the two
    password-backed principal rows this module operates on."""

    failed_attempts: int
    locked_until: datetime | None
    password_updated_at: datetime | None
    must_rotate_after: datetime | None


def is_locked(cred: LockableCredential, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    locked = cred.locked_until
    if locked is None:
        return False
    if locked.tzinfo is None:
        locked = locked.replace(tzinfo=UTC)
    return locked > now


def register_failure(cred: LockableCredential) -> None:
    """Increment the failure counter and apply exponential backoff past the
    threshold. Caller commits."""
    cred.failed_attempts = (cred.failed_attempts or 0) + 1
    if cred.failed_attempts >= _LOCK_THRESHOLD:
        over = cred.failed_attempts - _LOCK_THRESHOLD
        delay = min(_LOCK_BASE_SECONDS * (2**over), _LOCK_CAP_SECONDS)
        cred.locked_until = datetime.now(UTC) + timedelta(seconds=delay)


def reset_failures(cred: LockableCredential) -> None:
    cred.failed_attempts = 0
    cred.locked_until = None


def next_rotation_deadline(
    rotation_days: int | None, updated_at: datetime
) -> datetime | None:
    """Forced-rotation deadline for a password set at `updated_at`, or None when
    the tenant hasn't configured rotation. Every set-password path calls this so
    `must_rotate_after` actually reflects `password_rotation_days`."""
    if not rotation_days or rotation_days <= 0:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at + timedelta(days=rotation_days)


def rotation_due(cred: LockableCredential, now: datetime | None = None) -> bool:
    """True when a configured forced-rotation deadline has passed. NULL
    `must_rotate_after` (no rotation policy) is never due."""
    deadline = cred.must_rotate_after
    if deadline is None:
        return False
    now = now or datetime.now(UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= now


def credential_version(cred: LockableCredential) -> int:
    """Monotonic stamp that makes set-password tokens single-use: the password's
    last-update time in MICROSECONDS (second granularity would collide when a
    set-password lands in the same second as provisioning), 0 if never set."""
    ts = cred.password_updated_at
    if ts is None:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000)
