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

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import auth_events as EV
from app.core import credentials as CRED
from app.core import mfa
from app.core import passwords as PW
from app.core.breach_check import is_breached
from app.core.hr_auth import get_auth_policy
from app.core.portal_auth import (
    OTP_MAX_ATTEMPTS,
    get_current_member,
    hash_otp_code,
    issue_member_mfa_challenge_token,
    issue_member_set_password_token,
    issue_member_token,
    require_portal_tenant,
    resolve_member_credential,
    verify_member_mfa_challenge_token,
    verify_member_set_password_token,
)
from app.core.rate_limit import limiter
from app.core.request_context import client_ip
from app.core.settings import get_settings
from app.core.tenancy_host import TenantContext
from app.db.session import get_db
from app.models import Client, MemberAccount, MemberOtpCode
from app.models.auth import SUBJECT_MEMBER
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
from app.services.member_access import (
    CODE_ACCESS_ENDED,
    Capability,
    access_for_account,
    refusal,
)
from app.services.member_invite import clear_invite_expiry, invite_expired
from app.services.member_otp import as_utc, can_issue_otp, issue_otp, send_otp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])


def _accounts_for_email(
    db: Session, email: str, *, client_id: str | None = None
) -> list[MemberAccount]:
    """Non-disabled accounts on this email, optionally within ONE company.

    `request-code` is anonymous and matches across companies (an address can
    exist under several); `verify` passes `client_id` so the code is redeemed
    against the company whose portal the member is actually on — see the
    tenant note on `verify_code`.
    """
    conditions = [
        MemberAccount.email == email,
        MemberAccount.status != MEMBER_STATUS_DISABLED,
    ]
    if client_id is not None:
        conditions.append(MemberAccount.client_id == client_id)
    return list(db.execute(select(MemberAccount).where(*conditions)).scalars().all())


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
        # The magic link must name the company, or it lands on the pathless
        # sign-in, sends an empty tenant header and the emailed code cannot be
        # verified at all. Resolved per ACCOUNT rather than once: this endpoint
        # is anonymous and matches on email alone, so two accounts sharing an
        # address can belong to different companies.
        client = db.get(Client, account.client_id)
        send_otp(account, issued, client.slug if client else None)
        if settings.env == "dev" and settings.auth_mode == "mock":
            debug_code = issued.code

    # Always 202 — identical response whether or not the email exists.
    return OtpRequestOut(status="sent", debug_code=debug_code)


@router.post("/verify")
@limiter.limit("10/minute")
def verify_code(
    request: Request,
    body: OtpVerifyIn,
    tenant: TenantContext = Depends(require_portal_tenant),
    db: Session = Depends(get_db),
):
    """Verify an emailed sign-in code.

    A correct code proves control of the mailbox — it is the FIRST factor, not a
    complete sign-in. It therefore ends in the same three outcomes as
    `/login`: forced rotation, an MFA challenge, or a session. Returning a token
    straight from here let a member whose company requires 2FA skip the second
    factor entirely by choosing the emailed-code route (no `response_model`, so
    the challenge shapes can be returned like `/login` does).

    **`require_portal_tenant` is load-bearing, exactly as it is on `/login`.**
    It routes the Postgres `search_path` to the company's firm schema, and
    `_issue_member_login` below reads `policy_years`, `employees` and `claims` —
    all TENANT tables. Unrouted, every one of them resolves against `public`,
    which holds no tenant rows: the leaver check came back `unknown` and this
    route signed in members the password route refuses. It also scopes the
    lookup to ONE company, so a code minted for an address that exists under
    several can only be redeemed on the portal it was mailed for — otherwise the
    matched account and the routed schema could be different firms'.
    """
    email = body.email.strip().lower()
    code_hash = hash_otp_code(body.code.strip())
    now = datetime.now(UTC)
    invalid = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid or expired sign-in code."
    )

    matched: MemberAccount | None = None
    live_codes: list[tuple[MemberAccount, MemberOtpCode]] = []
    for account in _accounts_for_email(db, email, client_id=tenant.client_id):
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

    # **Commit the consumption before anything else can undo it.** Marking
    # `consumed_at` in the session is not enough: `_issue_member_login` rolls
    # back on a leaver refusal (it has to — `member_set_password` reaches it
    # holding a written credential), and that rollback reverted the consumption
    # too, leaving a correctly-guessed code live for the rest of its TTL. With
    # this commit the code is spent the moment it is matched, so neither
    # challenge below nor a refusal can replay it.
    db.commit()

    if CRED.rotation_due(matched):
        token = issue_member_set_password_token(
            matched.id, CRED.credential_version(matched)
        )
        EV.write_auth_event(
            db, event_type=EV.EVENT_PASSWORD_RESET_REQUEST, outcome=EV.OUTCOME_SUCCESS,
            surface="portal", subject_type=SUBJECT_MEMBER, subject_id=matched.id,
            client_id=matched.client_id, ip=_client_ip(request),
            subdomain=request.headers.get("host"), detail={"reason": "rotation_due"},
        )
        db.commit()
        return MemberChallengeOut(
            status="password_reset_required", challenge_token=token
        )

    policy = get_auth_policy(db, matched.client_id)
    if policy.mfa_portal_enabled and mfa.has_confirmed(
        db, SUBJECT_MEMBER, matched.id
    ):
        challenge = issue_member_mfa_challenge_token(matched.id, matched.client_id)
        db.commit()
        return MemberChallengeOut(challenge_token=challenge)

    return _issue_member_login(db, request, matched, matched.client_id)


# ── Credential login (username + password) ────────────────────────────────────
class MemberLoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)  # email / member id / staff id
    password: str = Field(min_length=1, max_length=256)


class MemberSetPasswordIn(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=1, max_length=256)


class MemberMfaIn(BaseModel):
    challenge_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=16)


class MemberChallengeOut(BaseModel):
    status: str = "mfa_required"
    challenge_token: str


_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")


# Aliased rather than re-implemented — see core/request_context.
_client_ip = client_ip


def _member_out(token: str, expires_at, account: MemberAccount) -> OtpVerifyOut:
    return OtpVerifyOut(
        token=token,
        expires_at=expires_at,
        member=PortalMemberOut.model_validate(account),
    )


def _issue_member_login(db: Session, request: Request, account: MemberAccount, client_id: str):
    # **The one choke point for every session this surface issues** — password
    # login, OTP verify, MFA and set-password all end here, so the leaver
    # refusal is checked once rather than at four call sites that could drift.
    #
    # It runs AFTER the credential has been proved, so it leaks nothing an
    # attacker could enumerate: the person already holds the password. And it
    # fires only on a definite `ended` verdict — a member with no roster row in
    # the current year resolves to `unknown` and signs in exactly as today,
    # because absence is not evidence that anyone left.
    access = access_for_account(
        db,
        member_account_id=account.id,
        client_id=client_id,
        staff_id=account.staff_id,
    )
    if access.state == "ended":
        account_id = account.id
        # **Discard whatever the caller had pending before recording anything.**
        # `member_set_password` reaches here having already written the new
        # `password_hash`, `password_updated_at`, `must_rotate_after`, a cleared
        # invite deadline and a reset failure count — committing those on the way
        # to a 403 would silently rotate the credential of a member being told
        # they cannot come in, and clear the invite deadline that bounds it. A
        # refusal must leave nothing behind but its own audit row.
        db.rollback()
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_BLOCKED,
            surface="portal", subject_type=SUBJECT_MEMBER, subject_id=account_id,
            client_id=client_id, ip=_client_ip(request),
            subdomain=request.headers.get("host"),
            detail={"reason": CODE_ACCESS_ENDED},
        )
        db.commit()
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, refusal(access, Capability.RECORD)
        )
    CRED.reset_failures(account)
    account.last_sign_in_at = datetime.now(UTC)
    if account.status == MEMBER_STATUS_INVITED:
        account.status = MEMBER_STATUS_ACTIVE
    EV.write_auth_event(
        db, event_type=EV.EVENT_LOGIN_SUCCESS, outcome=EV.OUTCOME_SUCCESS, surface="portal",
        subject_type=SUBJECT_MEMBER, subject_id=account.id, client_id=client_id,
        ip=_client_ip(request), subdomain=request.headers.get("host"),
    )
    token, expires_at = issue_member_token(
        account.id, client_id, CRED.credential_version(account)
    )
    db.commit()
    return _member_out(token, expires_at, account)


@router.post("/login")
@limiter.limit("10/minute")
def member_login(
    request: Request,
    body: MemberLoginIn,
    tenant: TenantContext = Depends(require_portal_tenant),
    db: Session = Depends(get_db),
):
    account = resolve_member_credential(db, tenant.client_id, body.identifier)
    if account is None or account.password_hash is None:
        PW.dummy_verify(body.password)
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_FAIL, surface="portal",
            client_id=tenant.client_id, identifier=body.identifier,
            ip=_client_ip(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise _INVALID
    if CRED.is_locked(account):
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOCKOUT, outcome=EV.OUTCOME_BLOCKED, surface="portal",
            subject_type=SUBJECT_MEMBER, subject_id=account.id, client_id=tenant.client_id,
            ip=_client_ip(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked. Try again later.")
    if not PW.verify_password(account.password_hash, body.password):
        CRED.register_failure(account)
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_FAIL, surface="portal",
            subject_type=SUBJECT_MEMBER, subject_id=account.id, client_id=tenant.client_id,
            identifier=body.identifier, ip=_client_ip(request),
            subdomain=request.headers.get("host"),
        )
        db.commit()
        raise _INVALID

    if PW.needs_rehash(account.password_hash):
        account.password_hash = PW.hash_password(body.password)

    # An emailed one-time password is bounded (`INVITE_TTL_DAYS`) — an unread
    # invite is otherwise a live credential sitting in a mailbox forever. The
    # deadline is cleared the moment a real password is set, so reaching it here
    # means the mailed value was never used. Checked AFTER verification so it
    # cannot be probed to discover which accounts have a pending invite.
    if invite_expired(account):
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_BLOCKED,
            surface="portal", subject_type=SUBJECT_MEMBER, subject_id=account.id,
            client_id=tenant.client_id, identifier=body.identifier,
            ip=_client_ip(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This invite has expired. Ask your HR team to send a new one.",
        )

    policy = get_auth_policy(db, tenant.client_id)

    # Forced rotation — the member proved the current password, so hand them a
    # self-serve set-password token rather than locking them out.
    if CRED.rotation_due(account):
        token = issue_member_set_password_token(
            account.id, CRED.credential_version(account)
        )
        EV.write_auth_event(
            db, event_type=EV.EVENT_PASSWORD_RESET_REQUEST, outcome=EV.OUTCOME_SUCCESS,
            surface="portal", subject_type=SUBJECT_MEMBER, subject_id=account.id,
            client_id=tenant.client_id, ip=_client_ip(request),
            subdomain=request.headers.get("host"), detail={"reason": "rotation_due"},
        )
        db.commit()
        return MemberChallengeOut(
            status="password_reset_required", challenge_token=token
        )

    if policy.mfa_portal_enabled and mfa.has_confirmed(db, SUBJECT_MEMBER, account.id):
        challenge = issue_member_mfa_challenge_token(account.id, tenant.client_id)
        db.commit()
        return MemberChallengeOut(challenge_token=challenge)
    return _issue_member_login(db, request, account, tenant.client_id)


@router.post("/mfa", response_model=OtpVerifyOut)
@limiter.limit("10/minute")
def member_mfa(
    request: Request,
    body: MemberMfaIn,
    tenant: TenantContext = Depends(require_portal_tenant),
    db: Session = Depends(get_db),
):
    try:
        member_id, cid = verify_member_mfa_challenge_token(body.challenge_token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Challenge expired.") from exc
    if cid != tenant.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")
    account = db.get(MemberAccount, member_id)
    if account is None or account.status == MEMBER_STATUS_DISABLED:
        raise _INVALID
    # Mirror `/login`'s lockout on the second factor too — per-IP limiting alone
    # let a six-digit code (accepted across a +/-1 step window) be ground out
    # from rotating IPs for the challenge's whole TTL.
    if CRED.is_locked(account):
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOCKOUT, outcome=EV.OUTCOME_BLOCKED, surface="portal",
            subject_type=SUBJECT_MEMBER, subject_id=member_id, client_id=tenant.client_id,
            ip=_client_ip(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(
            status.HTTP_423_LOCKED, "Account temporarily locked. Try again later."
        )
    ok = mfa.verify_totp(db, SUBJECT_MEMBER, member_id, body.code) or mfa.consume_recovery_code(
        db, SUBJECT_MEMBER, member_id, body.code
    )
    if not ok:
        CRED.register_failure(account)
        EV.write_auth_event(
            db, event_type=EV.EVENT_MFA_FAIL, outcome=EV.OUTCOME_FAIL, surface="portal",
            subject_type=SUBJECT_MEMBER, subject_id=member_id, client_id=tenant.client_id,
            ip=_client_ip(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code.")
    return _issue_member_login(db, request, account, tenant.client_id)


@router.post("/set-password")
@limiter.limit("10/minute")
def member_set_password(
    request: Request,
    body: MemberSetPasswordIn,
    tenant: TenantContext = Depends(require_portal_tenant),
    db: Session = Depends(get_db),
):
    try:
        member_id, version = verify_member_set_password_token(body.token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link expired or invalid.") from exc
    account = db.get(MemberAccount, member_id)
    if account is None or account.client_id != tenant.client_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link is not valid.")
    if CRED.credential_version(account) != version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link already used.")
    # A disabled account must not be reactivated via a set-password link
    # (mirrors the broker-side member_password_setup 409).
    if account.status == MEMBER_STATUS_DISABLED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Account is disabled — re-enable it first."
        )

    policy = get_auth_policy(db, tenant.client_id)
    ok, reason = PW.password_meets_policy(body.password, policy.password_min_entropy)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    if policy.breach_check_enabled and is_breached(body.password):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This password has appeared in a known data breach — choose another.",
        )
    account.password_hash = PW.hash_password(body.password)
    account.password_updated_at = datetime.now(UTC)
    # Restart the rotation clock from this reset (None when no policy).
    account.must_rotate_after = CRED.next_rotation_deadline(
        policy.password_rotation_days, account.password_updated_at
    )
    # The member now has a password of their own, so the mailed one-time
    # value's deadline no longer applies — leaving it set would expire the
    # password they just chose, locking them out of the account they just made.
    clear_invite_expiry(account)
    CRED.reset_failures(account)
    EV.write_auth_event(
        db, event_type=EV.EVENT_PASSWORD_RESET_COMPLETE, outcome=EV.OUTCOME_SUCCESS,
        surface="portal", subject_type=SUBJECT_MEMBER, subject_id=account.id,
        client_id=tenant.client_id, ip=_client_ip(request),
        subdomain=request.headers.get("host"),
    )
    # A reset link must not bypass the enrolled second factor.
    if policy.mfa_portal_enabled and mfa.has_confirmed(db, SUBJECT_MEMBER, account.id):
        challenge = issue_member_mfa_challenge_token(account.id, tenant.client_id)
        db.commit()
        return MemberChallengeOut(challenge_token=challenge)
    return _issue_member_login(db, request, account, tenant.client_id)


# ── Member MFA enrolment (authenticated, self-service) ─────────────────────────
class MemberMfaConfirmIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class MemberMfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/mfa/enroll/start")
def member_mfa_start(member=Depends(get_current_member), db: Session = Depends(get_db)):
    if not get_auth_policy(db, member.client_id).mfa_portal_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Two-factor authentication isn't enabled for your company.",
        )
    label = member.email or member.staff_id
    secret, uri = mfa.start_enrollment(db, SUBJECT_MEMBER, member.member_account_id, label)
    db.commit()
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/mfa/enroll/confirm")
def member_mfa_confirm(
    body: MemberMfaConfirmIn,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
):
    recovery = mfa.confirm_enrollment(
        db, SUBJECT_MEMBER, member.member_account_id, body.code.strip()
    )
    if recovery is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't match — try again.")
    db.commit()
    return {"status": "enrolled", "recovery_codes": recovery}


@router.post("/mfa/disable")
def member_mfa_disable(
    body: MemberMfaDisableIn,
    member=Depends(get_current_member),
    db: Session = Depends(get_db),
):
    account = db.get(MemberAccount, member.member_account_id)
    if (
        account is None
        or account.password_hash is None
        or not PW.verify_password(account.password_hash, body.password)
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password incorrect.")
    mfa.disable(db, SUBJECT_MEMBER, member.member_account_id)
    db.commit()
    return {"status": "disabled"}


@router.get("/security-status")
def member_security_status(member=Depends(get_current_member), db: Session = Depends(get_db)):
    return {
        "mfa_status": mfa.status_for(db, SUBJECT_MEMBER, member.member_account_id),
        "mfa_available": get_auth_policy(db, member.client_id).mfa_portal_enabled,
    }
