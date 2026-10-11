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
# Longest rotation period a company may set (ten years). Beyond it a deadline
# soon overflows `datetime`, which failed every set-password with a 500.
MAX_ROTATION_DAYS = 3650


class LockableCredential(Protocol):
    """Structural shape shared by AuthCredential and MemberAccount — the two
    password-backed principal rows this module operates on."""

    failed_attempts: int
    locked_until: datetime | None


class RotatingCredential(Protocol):
    @property
    def must_rotate_after(self) -> datetime | None: ...

    @property
    def password_updated_at(self) -> datetime | None: ...


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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def next_rotation_deadline(
    rotation_days: int | None, updated_at: datetime
) -> datetime | None:
    """Forced-rotation deadline for a password set at `updated_at`, or None when
    the tenant hasn't configured rotation. Set-password paths record it in
    `must_rotate_after`; `rotation_due` re-derives it from the CURRENT policy."""
    if not rotation_days or rotation_days <= 0:
        return None
    # Clamped as well as validated on write: a value stored before the bound
    # existed must not take set-password down.
    return _aware(updated_at) + timedelta(days=min(rotation_days, MAX_ROTATION_DAYS))


def rotation_due(
    cred: RotatingCredential, rotation_days: int | None, now: datetime | None = None,
) -> bool:
    """True when the password must be replaced before a session is issued.

    Two sources, kept apart:

    - an explicit forced flag: `must_rotate_after` at or before the password's
      own `password_updated_at` (a mailed invite or broker-set password is due
      on arrival), and
    - the company's current `rotation_days`, measured from when the password
      was set.

    A stored deadline LATER than the password's set time is the policy as it
    stood back then, so it is not trusted: a broker who enables, shortens or
    clears rotation must reach passwords that already exist.
    """
    now = now or datetime.now(UTC)
    updated_at = cred.password_updated_at
    forced = cred.must_rotate_after
    if forced is not None and (updated_at is None or _aware(forced) <= _aware(updated_at)):
        return _aware(forced) <= now
    if updated_at is None:
        return False
    deadline = next_rotation_deadline(rotation_days, updated_at)
    return deadline is not None and deadline <= now


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
