"""Shared credential-login primitives for any password-backed principal
(HR users + portal members): per-identifier lockout with exponential backoff,
the single-use set-password version stamp, and the newest-link-wins stamp that
lets a reissued set-password link cancel earlier ones.

These operate on the two password-backed principal rows, `AuthCredential` and
`MemberAccount` (structurally where only attributes are read).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NamedTuple, Protocol

from sqlalchemy import func, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.models import AuthCredential, MemberAccount

_LOCK_THRESHOLD = 5
_LOCK_BASE_SECONDS = 60
_LOCK_CAP_SECONDS = 3600


class LockableCredential(Protocol):
    """Structural shape shared by AuthCredential and MemberAccount — the two
    password-backed principal rows this module operates on."""

    failed_attempts: int
    locked_until: datetime | None


class RotatingCredential(Protocol):
    @property
    def must_rotate_after(self) -> datetime | None: ...


class VersionedCredential(Protocol):
    @property
    def password_updated_at(self) -> datetime | None: ...


class LinkStampedCredential(Protocol):
    password_token_issued_at: datetime | None


class SetPasswordClaim(NamedTuple):
    """A verified set-password token: whose it is, the password generation it
    was minted against, and when it was issued (`iat`, whole seconds)."""

    subject_id: str
    version: int
    issued_at: int


def is_locked(cred: LockableCredential, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    locked = cred.locked_until
    if locked is None:
        return False
    if locked.tzinfo is None:
        locked = locked.replace(tzinfo=UTC)
    return locked > now


def register_failure(db: Session, cred: AuthCredential | MemberAccount) -> int:
    """Count one failed attempt and apply exponential backoff past the
    threshold. Returns the new count. Caller commits.

    The increment is a single `UPDATE ... SET failed_attempts =
    failed_attempts + 1 RETURNING`, never a read-modify-write of the loaded
    row: concurrent guesses each read the same count and wrote back the same
    increment, so a burst of parallel attempts counted as one and stayed under
    the threshold.
    """
    model = type(cred)
    count = db.execute(
        update(model)
        .where(model.id == cred.id)
        .values(failed_attempts=func.coalesce(model.failed_attempts, 0) + 1)
        .returning(model.failed_attempts)
        .execution_options(synchronize_session=False)
    ).scalar_one()
    set_committed_value(cred, "failed_attempts", count)
    if count >= _LOCK_THRESHOLD:
        over = count - _LOCK_THRESHOLD
        delay = min(_LOCK_BASE_SECONDS * (2**over), _LOCK_CAP_SECONDS)
        cred.locked_until = datetime.now(UTC) + timedelta(seconds=delay)
    return int(count)


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


def rotation_due(cred: RotatingCredential, now: datetime | None = None) -> bool:
    """True when a configured forced-rotation deadline has passed. NULL
    `must_rotate_after` (no rotation policy) is never due."""
    deadline = cred.must_rotate_after
    if deadline is None:
        return False
    now = now or datetime.now(UTC)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= now


def credential_version(cred: VersionedCredential) -> int:
    """Monotonic stamp that makes set-password tokens single-use: the password's
    last-update time in MICROSECONDS (second granularity would collide when a
    set-password lands in the same second as provisioning), 0 if never set."""
    ts = cred.password_updated_at
    if ts is None:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp() * 1_000_000)


def stamp_password_token(cred: LinkStampedCredential) -> datetime:
    """Record that a new set-password link is being issued; return its issue time.

    Pass the result to the token issuer as `issued_at`, so the link's `iat` and
    the stamp agree. From then on `password_token_current` refuses every link
    issued earlier: without it, each reissue left all previous links redeemable
    until they expired. Caller commits.
    """
    issued_at = datetime.now(UTC)
    cred.password_token_issued_at = issued_at
    return issued_at


def password_token_current(cred: LinkStampedCredential, issued_at: int) -> bool:
    """False when a newer set-password link was issued after this one.

    `iat` has one-second resolution, so a link issued in the same second as the
    newest one stays valid. A NULL stamp (no link issued since the column was
    added) accepts any link, which keeps already-issued links working.
    """
    newest = cred.password_token_issued_at
    if newest is None:
        return True
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return issued_at >= int(newest.timestamp())
