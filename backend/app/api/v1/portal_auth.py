"""Public employee-portal auth: email OTP request + verify.

Registered in `main.py` WITHOUT the broker `require_write_access` gate and
without `get_current_user` — these are the only unauthenticated mutating
endpoints in the API, so they carry their own abuse guards: per-IP SlowAPI
limits plus per-account cooldown / hourly caps in `member_otp`.

`request-code` always answers 202 regardless of whether the email matches an
account (no account enumeration). An email can exist under multiple clients;
a code is issued per matching account and `verify` disambiguates by which
account's code matches.
"""
from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.portal_auth import OTP_MAX_ATTEMPTS, hash_otp_code, issue_member_token
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.session import get_db
from app.models import MemberAccount, MemberOtpCode
from app.models.member_account import (
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUS_DISABLED,
    MEMBER_STATUS_INVITED,
)
from app.schemas.portal import (
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    OtpVerifyOut,
    PortalMemberOut,
)
from app.services.member_otp import as_utc, can_issue_otp, issue_otp, send_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


def _accounts_for_email(db: Session, email: str) -> list[MemberAccount]:
    return list(
        db.execute(
            select(MemberAccount).where(
                MemberAccount.email == email,
                MemberAccount.status != MEMBER_STATUS_DISABLED,
            )
        ).scalars().all()
    )


@router.post(
    "/request-code",
    response_model=OtpRequestOut,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("5/minute")
def request_code(
    request: Request,
    body: OtpRequestIn,
    db: Session = Depends(get_db),
) -> OtpRequestOut:
    email = body.email.strip().lower()
    settings = get_settings()
    debug_code: str | None = None

    for account in _accounts_for_email(db, email):
        if not can_issue_otp(db, account):
            logger.info("OTP request throttled for account %s", account.id)
            continue
        issued = issue_otp(db, account)
        db.commit()
        send_otp(account, issued)
        if settings.env == "dev" and settings.auth_mode == "mock":
            debug_code = issued.code

    # Always 202 — identical response whether or not the email exists.
    return OtpRequestOut(status="sent", debug_code=debug_code)


@router.post("/verify", response_model=OtpVerifyOut)
@limiter.limit("10/minute")
def verify_code(
    request: Request,
    body: OtpVerifyIn,
    db: Session = Depends(get_db),
) -> OtpVerifyOut:
    email = body.email.strip().lower()
    code_hash = hash_otp_code(body.code.strip())
    now = datetime.now(UTC)
    invalid = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid or expired sign-in code."
    )

    matched: MemberAccount | None = None
    live_codes: list[tuple[MemberAccount, MemberOtpCode]] = []
    for account in _accounts_for_email(db, email):
        rows = db.execute(
            select(MemberOtpCode)
            .where(
                MemberOtpCode.member_account_id == account.id,
                MemberOtpCode.consumed_at.is_(None),
            )
            .order_by(MemberOtpCode.created_at.desc())
        ).scalars().all()
        for otp in rows:
            if (as_utc(otp.expires_at) or now) <= now:
                continue
            live_codes.append((account, otp))
            if hmac.compare_digest(otp.code_hash, code_hash):
                otp.consumed_at = now
                matched = account
                break
        if matched:
            break

    if matched is None:
        # Count the failure against the newest live code so repeated guessing
        # burns the code out after OTP_MAX_ATTEMPTS.
        if live_codes:
            _, newest = live_codes[0]
            newest.attempts += 1
            if newest.attempts >= OTP_MAX_ATTEMPTS:
                newest.consumed_at = now
            db.commit()
        raise invalid

    if matched.status == MEMBER_STATUS_INVITED:
        matched.status = MEMBER_STATUS_ACTIVE
    matched.last_sign_in_at = now
    db.commit()

    token, expires_at = issue_member_token(matched.id, matched.client_id)
    return OtpVerifyOut(
        token=token,
        expires_at=expires_at,
        member=PortalMemberOut.model_validate(matched),
    )
