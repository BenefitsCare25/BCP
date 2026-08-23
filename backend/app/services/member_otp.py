"""OTP issuance for portal member sign-in — shared by the public request-code
endpoint and broker-side invite/resend-invite provisioning."""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.mailer import get_mailer
from app.core.portal_auth import OTP_TTL_MINUTES, hash_otp_code
from app.core.settings import get_settings
from app.models import MemberAccount, MemberOtpCode

logger = logging.getLogger(__name__)

# Per-account abuse guards (the endpoint also has a per-IP SlowAPI limit).
OTP_COOLDOWN_SECONDS = 60
OTP_MAX_PER_HOUR = 5


@dataclass(frozen=True)
class IssuedOtp:
    code: str
    expires_at: datetime


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes, Postgres aware — normalize to aware UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _recent_codes(db: Session, account_id: str, since: datetime) -> list[MemberOtpCode]:
    rows = db.execute(
        select(MemberOtpCode).where(MemberOtpCode.member_account_id == account_id)
    ).scalars().all()
    return [c for c in rows if (as_utc(c.created_at) or since) > since]


def can_issue_otp(db: Session, account: MemberAccount) -> bool:
    """False while the account is inside the cooldown or over the hourly cap."""
    now = datetime.now(UTC)
    last_hour = _recent_codes(db, account.id, now - timedelta(hours=1))
    if len(last_hour) >= OTP_MAX_PER_HOUR:
        return False
    cutoff = now - timedelta(seconds=OTP_COOLDOWN_SECONDS)
    return not any((as_utc(c.created_at) or now) > cutoff for c in last_hour)


def issue_otp(db: Session, account: MemberAccount) -> IssuedOtp:
    """Create + persist a fresh code (does NOT commit — caller owns the txn)."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(UTC) + timedelta(minutes=OTP_TTL_MINUTES)
    db.add(
        MemberOtpCode(
            member_account_id=account.id,
            code_hash=hash_otp_code(code),
            expires_at=expires_at,
        )
    )
    return IssuedOtp(code=code, expires_at=expires_at)


def magic_link(email: str, code: str, slug: str | None = None) -> str:
    """The one-click sign-in URL mailed beside the code.

    Carries the company in the PATH, like `portal_sign_in_url`. Without it the
    link lands on the pathless sign-in, which sends an EMPTY tenant header — so
    `require_portal_tenant` 400s and the emailed code cannot be verified at all
    until the member types a company code, which is the exact failure the path
    form exists to remove.
    """
    origin = get_settings().frontend_origin.rstrip("/")
    query = urlencode({"email": email, "code": code})
    base = f"{origin}/portal/{slug}/sign-in" if slug else f"{origin}/portal/sign-in"
    return f"{base}?{query}"


def send_otp(
    account: MemberAccount, issued: IssuedOtp, slug: str | None = None
) -> bool:
    """Deliver the code; a mail failure is logged, never raised.

    Returns True when the mailer accepted the message. The ANONYMOUS
    request-code endpoint must ignore the return value (its response can't
    reveal whether the account exists or the send worked); broker-side
    invite/resend endpoints surface it as `mail_sent` so a dead SMTP config
    doesn't masquerade as a successful rollout.

    `slug` is the member's company alias, which the magic link must carry — see
    `magic_link`. Optional so a caller with no tenant to hand still sends a
    usable CODE; only the one-click link degrades.
    """
    email = (account.email or "").strip()
    if not email:
        logger.error("Cannot send portal OTP for account %s without email", account.id)
        return False
    try:
        get_mailer().send_otp(
            email,
            issued.code,
            magic_link(email, issued.code, slug),
        )
        return True
    except Exception:
        logger.exception("Failed to send portal OTP to %s", account.email)
        return False
