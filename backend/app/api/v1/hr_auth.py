"""Public HR credential-login endpoints (`{slug}.hr.<base>`).

Registered in `main.py` OUTSIDE the broker `require_write_access` gate (like
`portal_auth`) — these authenticate an HR admin from scratch. Every mutating
step carries per-IP SlowAPI limits and per-identifier lockout, and writes a
tenant-tagged `auth_events` row.

Flow: login (password) → optional TOTP step (enrolled users) → short access
token in the body + rotating refresh token in a host-only cookie. See
`docs/AUTH_DESIGN.md` §5.2.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import auth_events as EV
from app.core import hr_auth as HR
from app.core import passwords as PW
from app.core import sessions as SESS
from app.core.breach_check import is_breached
from app.core.rate_limit import limiter
from app.core.tenancy_host import TenantContext
from app.db.session import get_db
from app.models.auth import SUBJECT_USER
from app.models.user import USER_STATUS_ACTIVE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hr/auth", tags=["hr-auth"])

_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


# ── Schemas ────────────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    identifier: str = Field(min_length=1, max_length=320)  # email OR HR login id
    password: str = Field(min_length=1, max_length=256)


class HrMeOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: str
    client_id: str
    company_name: str | None = None
    mfa_status: str = "none"  # none | pending | confirmed
    mfa_available: bool = False  # broker has enabled 2FA for the HR surface


class MfaStartOut(BaseModel):
    secret: str
    otpauth_uri: str


class MfaConfirmIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)


class MfaConfirmOut(BaseModel):
    status: str = "enrolled"
    recovery_codes: list[str]


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    status: str = "authenticated"
    access_token: str
    expires_at: datetime
    mfa_enrollment_required: bool = False
    me: HrMeOut


class LoginChallengeOut(BaseModel):
    status: str  # "mfa_required" | "password_reset_required"
    challenge_token: str | None = None


class MfaVerifyIn(BaseModel):
    challenge_token: str = Field(min_length=1)
    code: str = Field(min_length=1, max_length=16)


class SetPasswordIn(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=1, max_length=256)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _company_name(db: Session, client_id: str) -> str | None:
    from app.models import Client

    c = db.get(Client, client_id)
    return c.name if c else None


def _me(db: Session, user, client_id: str) -> HrMeOut:
    return HrMeOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        client_id=client_id,
        company_name=_company_name(db, client_id),
        mfa_status=HR.user_mfa_status(db, user.id),
        mfa_available=HR.get_auth_policy(db, client_id).mfa_hr_enabled,
    )


def _issue_login(
    db: Session,
    response: Response,
    request: Request,
    user,
    tenant: TenantContext,
    *,
    mfa_enrollment_required: bool = False,
) -> TokenOut:
    """Mint access + refresh for a fully-authenticated HR user. Commits."""
    policy = HR.get_auth_policy(db, tenant.client_id)
    access, exp = HR.issue_hr_access_token(
        user_id=user.id,
        client_id=tenant.client_id,
        broker_firm_id=tenant.broker_firm_id,
        role=user.role,
    )
    issued = SESS.issue_session(
        db,
        subject_type=SUBJECT_USER,
        subject_id=user.id,
        client_id=tenant.client_id,
        broker_firm_id=tenant.broker_firm_id,
        absolute_hours=policy.session_absolute_hours,
        ip=_client_ip(request),
        user_agent=_ua(request),
        subdomain=request.headers.get("host"),
    )
    EV.write_auth_event(
        db,
        event_type=EV.EVENT_LOGIN_SUCCESS,
        outcome=EV.OUTCOME_SUCCESS,
        surface="hr",
        subject_type=SUBJECT_USER,
        subject_id=user.id,
        client_id=tenant.client_id,
        broker_firm_id=tenant.broker_firm_id,
        ip=_client_ip(request),
        user_agent=_ua(request),
        subdomain=request.headers.get("host"),
        detail={"mfa_enrollment_required": mfa_enrollment_required},
    )
    db.commit()
    HR.set_refresh_cookie(response, issued.token, issued.expires_at)
    return TokenOut(
        access_token=access,
        expires_at=exp,
        mfa_enrollment_required=mfa_enrollment_required,
        me=_me(db, user, tenant.client_id),
    )


# ── Login ──────────────────────────────────────────────────────────────────────
@router.post("/login")
@limiter.limit("10/minute")
def login(
    request: Request,
    body: LoginIn,
    response: Response,
    tenant: TenantContext = Depends(HR.require_hr_tenant),
    db: Session = Depends(get_db),
):
    now = datetime.now(UTC)
    resolved = HR.resolve_hr_credential(db, tenant, body.identifier)

    if resolved is None:
        PW.dummy_verify(body.password)  # constant-time no-user path
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_FAIL, surface="hr",
            client_id=tenant.client_id, broker_firm_id=tenant.broker_firm_id,
            identifier=body.identifier, ip=_client_ip(request), user_agent=_ua(request),
            subdomain=request.headers.get("host"), detail={"reason": "no_user"},
        )
        db.commit()
        raise _INVALID

    user, cred = resolved

    if HR.is_locked(cred, now):
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOCKOUT, outcome=EV.OUTCOME_BLOCKED, surface="hr",
            subject_type=SUBJECT_USER, subject_id=user.id, client_id=tenant.client_id,
            broker_firm_id=tenant.broker_firm_id, ip=_client_ip(request),
            user_agent=_ua(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(
            status.HTTP_423_LOCKED,
            "Account temporarily locked after repeated failures. Try again later.",
        )

    password_ok = PW.verify_password(cred.password_hash, body.password)
    if not password_ok or user.status != USER_STATUS_ACTIVE:
        HR.register_failure(cred)
        EV.write_auth_event(
            db, event_type=EV.EVENT_LOGIN_FAIL, outcome=EV.OUTCOME_FAIL, surface="hr",
            subject_type=SUBJECT_USER, subject_id=user.id, client_id=tenant.client_id,
            broker_firm_id=tenant.broker_firm_id, identifier=body.identifier,
            ip=_client_ip(request), user_agent=_ua(request),
            subdomain=request.headers.get("host"), detail={"reason": "bad_password"},
        )
        db.commit()
        raise _INVALID

    # Password correct.
    HR.reset_failures(cred)
    cred.last_login_at = now
    if PW.needs_rehash(cred.password_hash):
        cred.password_hash = PW.hash_password(body.password)

    # Forced rotation — the password is past its configured rotation deadline.
    if HR.rotation_due(cred, now):
        token = HR.issue_set_password_token(user.id, HR.credential_version(cred))
        db.commit()
        return LoginChallengeOut(status="password_reset_required", challenge_token=token)

    # MFA — challenge only when the company has 2FA enabled AND this user has
    # actually enrolled (enrolment is self-service + optional).
    policy = HR.get_auth_policy(db, tenant.client_id)
    enrolled = HR.user_has_confirmed_mfa(db, user.id)
    if policy.mfa_hr_enabled and enrolled:
        challenge = HR.issue_mfa_challenge_token(user.id, tenant.client_id)
        EV.write_auth_event(
            db, event_type=EV.EVENT_MFA_CHALLENGE, outcome=EV.OUTCOME_SUCCESS, surface="hr",
            subject_type=SUBJECT_USER, subject_id=user.id, client_id=tenant.client_id,
            broker_firm_id=tenant.broker_firm_id, ip=_client_ip(request),
            user_agent=_ua(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        return LoginChallengeOut(status="mfa_required", challenge_token=challenge)

    # 2FA is on but this user hasn't enrolled — issue the session yet flag that
    # enrolment is required, so the shell forces set-up before real work.
    return _issue_login(
        db, response, request, user, tenant,
        mfa_enrollment_required=policy.mfa_hr_enabled and not enrolled,
    )


# ── MFA step ───────────────────────────────────────────────────────────────────
@router.post("/mfa", response_model=TokenOut)
@limiter.limit("10/minute")
def verify_mfa(
    request: Request,
    body: MfaVerifyIn,
    response: Response,
    tenant: TenantContext = Depends(HR.require_hr_tenant),
    db: Session = Depends(get_db),
):
    from app.models import User

    try:
        user_id, cid = HR.verify_mfa_challenge_token(body.challenge_token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Challenge expired.") from exc
    if cid != tenant.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")

    user = db.get(User, user_id)
    if user is None or user.role not in HR.HR_ROLES or user.status != USER_STATUS_ACTIVE:
        raise _INVALID
    # A TOTP code OR a single-use recovery code satisfies the challenge.
    ok = HR.verify_user_totp(db, user_id, body.code) or HR.consume_recovery_code(
        db, user_id, body.code
    )
    if not ok:
        EV.write_auth_event(
            db, event_type=EV.EVENT_MFA_FAIL, outcome=EV.OUTCOME_FAIL, surface="hr",
            subject_type=SUBJECT_USER, subject_id=user_id, client_id=tenant.client_id,
            broker_firm_id=tenant.broker_firm_id, ip=_client_ip(request),
            user_agent=_ua(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication code.")
    return _issue_login(db, response, request, user, tenant)


# ── MFA enrolment (authenticated, self-service) ────────────────────────────────
@router.post("/mfa/enroll/start", response_model=MfaStartOut)
def mfa_enroll_start(
    current=Depends(HR.get_current_hr_user),
    db: Session = Depends(get_db),
) -> MfaStartOut:
    if not HR.get_auth_policy(db, current.client_id).mfa_hr_enabled:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Two-factor authentication isn't enabled for your company.",
        )
    secret, uri = HR.start_user_mfa_enrollment(
        db, current.user_id, current.email or current.user_id
    )
    db.commit()
    return MfaStartOut(secret=secret, otpauth_uri=uri)


@router.post("/mfa/enroll/confirm", response_model=MfaConfirmOut)
def mfa_enroll_confirm(
    body: MfaConfirmIn,
    request: Request,
    current=Depends(HR.get_current_hr_user),
    db: Session = Depends(get_db),
) -> MfaConfirmOut:
    recovery = HR.confirm_user_mfa_enrollment(db, current.user_id, body.code.strip())
    if recovery is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't match — try again.")
    EV.write_auth_event(
        db, event_type=EV.EVENT_MFA_SUCCESS, outcome=EV.OUTCOME_SUCCESS, surface="hr",
        subject_type=SUBJECT_USER, subject_id=current.user_id, client_id=current.client_id,
        broker_firm_id=current.broker_firm_id, ip=_client_ip(request),
        user_agent=_ua(request), subdomain=request.headers.get("host"),
        detail={"event": "mfa_enrolled"},
    )
    db.commit()
    return MfaConfirmOut(recovery_codes=recovery)


@router.post("/mfa/disable", status_code=200)
def mfa_disable(
    body: MfaDisableIn,
    current=Depends(HR.get_current_hr_user),
    db: Session = Depends(get_db),
):
    from app.models import AuthCredential

    # Re-authenticate with the password before removing a security factor.
    cred = (
        db.query(AuthCredential)
        .filter(AuthCredential.user_id == current.user_id)
        .one_or_none()
    )
    if cred is None or not PW.verify_password(cred.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password incorrect.")
    HR.disable_user_mfa(db, current.user_id)
    db.commit()
    return {"status": "disabled"}


# ── Refresh ────────────────────────────────────────────────────────────────────
@router.post("/refresh", response_model=TokenOut)
@limiter.limit("30/minute")
def refresh(
    request: Request,
    response: Response,
    tenant: TenantContext = Depends(HR.require_hr_tenant),
    db: Session = Depends(get_db),
):
    from app.models import User

    token = request.cookies.get(HR.REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No session.")
    policy = HR.get_auth_policy(db, tenant.client_id)
    result = SESS.rotate_session(
        db, token, absolute_hours=policy.session_absolute_hours,
        idle_minutes=policy.session_idle_minutes,
        ip=_client_ip(request), user_agent=_ua(request),
        subdomain=request.headers.get("host"),
    )
    if result.reuse_detected:
        EV.write_auth_event(
            db, event_type=EV.EVENT_TOKEN_REUSE, outcome=EV.OUTCOME_BLOCKED, surface="hr",
            client_id=tenant.client_id, broker_firm_id=tenant.broker_firm_id,
            ip=_client_ip(request), user_agent=_ua(request),
            subdomain=request.headers.get("host"),
        )
        db.commit()
        HR.clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session revoked. Sign in again.")
    if result.session is None:
        db.commit()
        HR.clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired.")

    from app.models import AuthSession

    child = result.session
    # Resolve the subject from the freshly-issued child row and re-verify the
    # session is pinned to the subdomain's tenant.
    row = db.get(AuthSession, child.session_id)
    if row is None or row.client_id != tenant.client_id:
        db.commit()
        HR.clear_refresh_cookie(response)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")
    user = db.get(User, row.subject_id)
    if user is None or user.role not in HR.HR_ROLES or user.status != USER_STATUS_ACTIVE:
        db.commit()
        HR.clear_refresh_cookie(response)
        raise _INVALID

    access, exp = HR.issue_hr_access_token(
        user_id=user.id, client_id=tenant.client_id,
        broker_firm_id=tenant.broker_firm_id, role=user.role,
    )
    EV.write_auth_event(
        db, event_type=EV.EVENT_TOKEN_REFRESH, outcome=EV.OUTCOME_SUCCESS, surface="hr",
        subject_type=SUBJECT_USER, subject_id=user.id, client_id=tenant.client_id,
        broker_firm_id=tenant.broker_firm_id, ip=_client_ip(request),
        user_agent=_ua(request), subdomain=request.headers.get("host"),
    )
    db.commit()
    HR.set_refresh_cookie(response, child.token, child.expires_at)
    return TokenOut(access_token=access, expires_at=exp, me=_me(db, user, tenant.client_id))


# ── Logout ─────────────────────────────────────────────────────────────────────
@router.post("/logout", status_code=200)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(HR.REFRESH_COOKIE_NAME)
    if token:
        SESS.revoke_token(db, token)
        db.commit()
    HR.clear_refresh_cookie(response)
    return {"status": "signed_out"}


# ── Set / reset password ───────────────────────────────────────────────────────
@router.post("/set-password")
@limiter.limit("10/minute")
def set_password(
    request: Request,
    body: SetPasswordIn,
    response: Response,
    tenant: TenantContext = Depends(HR.require_hr_tenant),
    db: Session = Depends(get_db),
):
    from app.models import AuthCredential, User, UserClientAccess
    from app.models.user import USER_STATUS_INVITED

    try:
        user_id, version = HR.verify_set_password_token(body.token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link expired or invalid.") from exc

    user = db.get(User, user_id)
    cred = (
        db.query(AuthCredential).filter(AuthCredential.user_id == user_id).one_or_none()
        if user
        else None
    )
    grant = (
        db.query(UserClientAccess.id)
        .filter(
            UserClientAccess.user_id == user_id,
            UserClientAccess.client_id == tenant.client_id,
        )
        .scalar()
        if user
        else None
    )
    if user is None or cred is None or grant is None or user.role not in HR.HR_ROLES:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link is not valid.")
    # Single-use: the token's version must match the credential's current stamp.
    if HR.credential_version(cred) != version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Reset link already used.")

    policy = HR.get_auth_policy(db, tenant.client_id)
    ok, reason = PW.password_meets_policy(body.password, policy.password_min_entropy)
    if not ok:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)
    if policy.breach_check_enabled and is_breached(body.password):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This password has appeared in a known data breach — choose another.",
        )

    cred.password_hash = PW.hash_password(body.password)
    cred.password_updated_at = datetime.now(UTC)
    # Start the next rotation clock from this reset (None when no policy).
    cred.must_rotate_after = HR.next_rotation_deadline(
        policy.password_rotation_days, cred.password_updated_at
    )
    HR.reset_failures(cred)
    if user.status == USER_STATUS_INVITED:
        user.status = USER_STATUS_ACTIVE
    EV.write_auth_event(
        db, event_type=EV.EVENT_PASSWORD_RESET_COMPLETE, outcome=EV.OUTCOME_SUCCESS,
        surface="hr", subject_type=SUBJECT_USER, subject_id=user.id,
        client_id=tenant.client_id, broker_firm_id=tenant.broker_firm_id,
        ip=_client_ip(request), user_agent=_ua(request),
        subdomain=request.headers.get("host"),
    )

    # A reset link must NOT be a way around the enrolled second factor: if 2FA is
    # on and this user has confirmed TOTP, hand back an MFA challenge instead of a
    # full session — identical to the login path.
    enrolled = HR.user_has_confirmed_mfa(db, user.id)
    if policy.mfa_hr_enabled and enrolled:
        challenge = HR.issue_mfa_challenge_token(user.id, tenant.client_id)
        EV.write_auth_event(
            db, event_type=EV.EVENT_MFA_CHALLENGE, outcome=EV.OUTCOME_SUCCESS, surface="hr",
            subject_type=SUBJECT_USER, subject_id=user.id, client_id=tenant.client_id,
            broker_firm_id=tenant.broker_firm_id, ip=_client_ip(request),
            user_agent=_ua(request), subdomain=request.headers.get("host"),
        )
        db.commit()
        return LoginChallengeOut(status="mfa_required", challenge_token=challenge)
    # commit happens inside _issue_login
    return _issue_login(
        db, response, request, user, tenant,
        mfa_enrollment_required=policy.mfa_hr_enabled and not enrolled,
    )


# ── Me ─────────────────────────────────────────────────────────────────────────
@router.get("/me", response_model=HrMeOut)
def me(
    current=Depends(HR.get_current_hr_user),
    db: Session = Depends(get_db),
):
    from app.models import User

    user = db.get(User, current.user_id)
    return _me(db, user, current.client_id)
