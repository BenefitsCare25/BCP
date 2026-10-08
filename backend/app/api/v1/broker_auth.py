"""Broker staff sign-in: Microsoft token exchange, password sign-in with a
mandatory authenticator, invitation links, per-account MFA and revocable
sessions.

Every route answers only on hosts that serve the staff app, and every session
is bound to the broker firm the host names (`core/broker_auth.bound_to_firm`):
an account signs in only on its own firm's hosts, the master admin only on a
platform host. Which methods a firm offers is `core/identity_providers.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import auth_events as events
from app.core import broker_auth as BA
from app.core import credentials as CRED
from app.core import mfa, sessions
from app.core import passwords as PW
from app.core.auth import _entra_principal
from app.core.breach_check import is_breached
from app.core.cookie_auth import require_same_origin
from app.core.identity_providers import firm_sign_in_methods
from app.core.rate_limit import limiter
from app.core.request_context import client_ip, user_agent
from app.core.settings import get_settings
from app.core.tenant_resolution import FirmContext, request_firm, require_staff_surface
from app.db.session import get_db
from app.models import AuthCredential, AuthSession, User
from app.models.platform import IDP_ENTRA, IDP_LOCAL
from app.models.user import USER_STATUS_ACTIVE
from app.services.brand import resolve_staff_brand

router = APIRouter(
    prefix="/broker/auth",
    tags=["broker authentication"],
    dependencies=[Depends(require_staff_surface)],
)

# The platform's password policy for staff (the company defaults HR accounts
# start from): at least `passwords.MIN_LENGTH` characters, this entropy floor,
# and not in a known breach.
STAFF_PASSWORD_MIN_ENTROPY = 60
_INVALID_CREDENTIALS = "Invalid credentials."
_LOCKED = "Account temporarily locked after repeated failures. Try again later."
_CODE_MISMATCH = "That code did not match. Try again."


class Exchange(BaseModel):
    access_token: str = Field(min_length=1, max_length=16384)
    # An invitation link's token: binds this Microsoft identity to the invited
    # account when the identity is not bound to any account yet.
    invite_token: str | None = Field(default=None, min_length=16, max_length=256)


class Code(BaseModel):
    code: str = Field(pattern=r"^(?:[0-9]{6}|[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4})$")


class LocalLogin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class LoginCode(Code):
    challenge_token: str = Field(min_length=1, max_length=2048)


class AcceptInvite(BaseModel):
    invite_token: str = Field(min_length=16, max_length=256)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=255)


def _invalid() -> HTTPException:
    """The generic credential 401, a fresh instance per raise (a shared one
    would keep every raise's frames, passwords included, alive)."""
    return HTTPException(401, _INVALID_CREDENTIALS)


def _auth(request: Request, db: Session) -> BA.Authenticated:
    return BA.authenticate(
        request.headers.get("authorization"), db, lock_user=True, firm=request_firm(request)
    )


def _require_mfa(required: bool) -> None:
    if not required:
        raise HTTPException(
            409, "Your administrator has not required an authenticator for this account."
        )


def _account_bucket(request: Request) -> str:
    # A signed subject controls the account bucket; rotating IPs/sessions do not.
    try:
        claims = BA.decode(BA.bearer(request.headers.get("authorization")))
        return "broker-mfa:" + str(claims["sub"])
    except HTTPException:
        return "broker-mfa-invalid:" + (client_ip(request) or "unknown")


def _event(
    db: Session, request: Request, user: User, event: str, outcome: str,
    detail: dict[str, Any] | None = None,
) -> None:
    events.write_auth_event(
        db,
        surface="broker",
        subject_type=BA.SUBJECT,
        subject_id=user.id,
        broker_firm_id=user.broker_firm_id,
        event_type=event,
        outcome=outcome,
        ip=client_ip(request),
        user_agent=user_agent(request),
        detail=detail,
    )


def _anonymous_failure(
    db: Session, request: Request, *, firm_id: str | None = None,
    identifier: str | None = None, reason: str | None = None,
) -> None:
    """A failed sign-in with no account to file it under. Commits."""
    events.write_auth_event(
        db,
        surface="broker",
        event_type=events.EVENT_LOGIN_FAIL,
        outcome=events.OUTCOME_FAIL,
        broker_firm_id=firm_id,
        identifier=identifier,
        ip=client_ip(request),
        user_agent=user_agent(request),
        detail={"reason": reason} if reason else None,
    )
    db.commit()


def _refuse_other_firm(db: Session, request: Request, user: User) -> None:
    """403 when the account may not sign in on this host's firm. Commits the
    refusal's audit row; issues nothing."""
    firm = request_firm(request)
    if BA.bound_to_firm(user, BA.session_firm(user), firm):
        return
    _event(db, request, user, events.EVENT_LOGIN_FAIL, events.OUTCOME_BLOCKED,
           detail={"reason": "other_firm", "host": firm.host if firm else None})
    db.commit()
    raise HTTPException(403, {
        "code": "no_access", "message": "This account belongs to a different organisation.",
    })


def _start_session(
    db: Session, request: Request, response: Response, user: User, *,
    mfa_verified: bool = False,
) -> dict[str, Any]:
    """Issue a session for a signed-in account, with its cookie. Commits.

    An account that needs the authenticator and has not just passed it gets a
    five-minute session that reaches only the `/broker/auth/mfa*` endpoints.
    """
    policy = BA.staff_policy(db, user)
    idp = BA.user_idp(user)
    if not policy.method_enabled:
        db.rollback()
        if idp == IDP_LOCAL:
            raise _invalid()
        raise BA.sign_in_method_disabled(
            "Microsoft sign-in is not enabled for this organisation."
        )
    pending = policy.authenticator_required and not mfa_verified
    issued = sessions.issue_session(
        db,
        subject_type=BA.SUBJECT,
        subject_id=user.id,
        client_id=None,
        broker_firm_id=BA.session_firm(user),
        absolute_hours=BA.ABSOLUTE_HOURS,
        expires_at=(datetime.now(UTC) + timedelta(minutes=BA.PENDING_MFA_MINUTES)
                    if pending else None),
        mfa_verified=mfa_verified,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    row = db.get(AuthSession, issued.session_id)
    assert row is not None
    if mfa_verified:
        _event(db, request, user, events.EVENT_MFA_SUCCESS, events.OUTCOME_SUCCESS)
    _event(db, request, user, events.EVENT_MFA_CHALLENGE if pending
           else events.EVENT_LOGIN_SUCCESS, events.OUTCOME_SUCCESS, detail={"method": idp})
    db.commit()
    BA.set_cookie(response, issued.token, issued.expires_at)
    return BA.response_session(user, row, mfa_required=policy.authenticator_required)


# ── Microsoft ─────────────────────────────────────────────────────────────────
@router.post("/exchange")
@limiter.limit("10/minute")
def exchange(
    body: Exchange, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    require_same_origin(request)
    if get_settings().auth_mode != "entra":
        raise HTTPException(404, "Microsoft sign-in is not enabled.")
    try:
        principal = _entra_principal(
            "Bearer " + body.access_token, db, firm=request_firm(request),
            invite_token=body.invite_token,
        )
    except HTTPException as exc:
        reason = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        _anonymous_failure(db, request, reason=reason)
        raise
    user = BA.active_user(db, principal.user_id, lock=True, idp=IDP_ENTRA)
    _refuse_other_firm(db, request, user)
    return _start_session(db, request, response, user)


# ── Email + password ──────────────────────────────────────────────────────────
def _local_firm(db: Session, firm: FirmContext | None) -> str | None:
    """The host's firm when it offers password sign-in, else None."""
    if firm is None or not firm_sign_in_methods(db, firm.firm_id).local:
        return None
    return firm.firm_id


def _refuse_locked(db: Session, request: Request, user: User, cred: AuthCredential) -> None:
    if CRED.is_locked(cred):
        _event(db, request, user, events.EVENT_LOCKOUT, events.OUTCOME_BLOCKED)
        db.commit()
        raise HTTPException(423, _LOCKED)


@router.post("/login")
@limiter.limit("10/minute")
def login(
    body: LocalLogin, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Password sign-in. The authenticator is mandatory: an enrolled account
    gets a challenge for `/login/mfa`, any other account a session that can
    only enrol one. Every failure is the same 401."""
    require_same_origin(request)
    firm = request_firm(request)
    email = body.email.strip().lower()
    firm_id = _local_firm(db, firm)
    found = BA.local_account(db, firm_id, email=email) if firm_id else None
    if firm_id is None or found is None:
        PW.dummy_verify(body.password)  # the no-account path costs the same
        _anonymous_failure(
            db, request, firm_id=firm.firm_id if firm else None, identifier=email,
            reason="no_user" if firm_id else "method_disabled",
        )
        raise _invalid()
    user, cred = found
    _refuse_locked(db, request, user, cred)
    password_ok = PW.verify_password(cred.password_hash, body.password)
    if not password_ok or user.status != USER_STATUS_ACTIVE:
        CRED.register_failure(db, cred)
        _event(db, request, user, events.EVENT_LOGIN_FAIL, events.OUTCOME_FAIL,
               detail={"reason": "bad_password"})
        db.commit()
        raise _invalid()
    if PW.needs_rehash(cred.password_hash):
        cred.password_hash = PW.hash_password(body.password)
    if mfa.has_confirmed(db, BA.SUBJECT, user.id):
        challenge = BA.issue_mfa_challenge(user.id, firm_id)
        _event(db, request, user, events.EVENT_MFA_CHALLENGE, events.OUTCOME_SUCCESS,
               detail={"method": IDP_LOCAL})
        db.commit()
        response.headers["Cache-Control"] = "no-store"
        return {"mfa_required": True, "challenge_token": challenge}
    CRED.reset_failures(cred)
    cred.last_login_at = datetime.now(UTC)
    return _start_session(db, request, response, user)


@router.post("/login/mfa")
@limiter.limit("10/minute")
def login_mfa(
    body: LoginCode, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """The second step of password sign-in: a TOTP or a single-use recovery code."""
    require_same_origin(request)
    # A challenge that is expired, forged, from another firm's host, or whose
    # account can no longer sign in this way: the person starts again.
    expired = HTTPException(401, {
        "code": "challenge_expired", "message": "Your sign-in expired. Sign in again.",
    })
    try:
        user_id, firm_id = BA.verify_mfa_challenge(body.challenge_token)
    except jwt.InvalidTokenError as exc:
        raise expired from exc
    host_firm = _local_firm(db, request_firm(request))
    found = BA.local_account(db, firm_id, user_id=user_id) if host_firm == firm_id else None
    if found is None or found[0].status != USER_STATUS_ACTIVE:
        raise expired
    user, cred = found
    # The second factor shares the password's lockout: per-IP limits alone
    # would let rotating addresses grind codes for the challenge's lifetime.
    _refuse_locked(db, request, user, cred)
    if not (
        mfa.verify_totp(db, BA.SUBJECT, user.id, body.code)
        or mfa.consume_recovery_code(db, BA.SUBJECT, user.id, body.code)
    ):
        CRED.register_failure(db, cred)
        _event(db, request, user, events.EVENT_MFA_FAIL, events.OUTCOME_FAIL)
        db.commit()
        raise HTTPException(401, _CODE_MISMATCH)
    CRED.reset_failures(cred)
    cred.last_login_at = datetime.now(UTC)
    return _start_session(db, request, response, user, mfa_verified=True)


def _password_meets_policy(password: str) -> None:
    ok, reason = PW.password_meets_policy(password, STAFF_PASSWORD_MIN_ENTROPY)
    if not ok:
        raise HTTPException(422, reason)
    if is_breached(password):
        raise HTTPException(
            422, "This password has appeared in a known data breach — choose another."
        )


@router.post("/accept-invite")
@limiter.limit("10/minute")
def accept_invite(
    body: AcceptInvite, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Accept an invitation link by choosing a password.

    Activates the invited account and consumes the invitation, then returns a
    session that can only enrol the authenticator, which is next.
    """
    require_same_origin(request)
    firm = request_firm(request)
    invitation, user = BA.pending_staff_invitation(
        db, body.invite_token, firm, roles=BA.LOCAL_ROLES
    )
    if user.external_id is not None or BA.credential(db, user.id) is not None:
        raise BA.invitation_invalid()
    if not firm_sign_in_methods(db, invitation.broker_firm_id).local:
        raise BA.sign_in_method_disabled(
            "Password sign-in is not enabled for this organisation."
        )
    _password_meets_policy(body.password)
    BA.consume_invitation(db, invitation)
    now = datetime.now(UTC)
    db.add(AuthCredential(
        user_id=user.id, broker_firm_id=user.broker_firm_id,
        password_hash=PW.hash_password(body.password), password_updated_at=now,
        last_login_at=now,
    ))
    display_name = (body.display_name or "").strip()
    if display_name:
        user.display_name = display_name
    user.status = USER_STATUS_ACTIVE
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise BA.invitation_invalid() from None
    _event(db, request, user, events.EVENT_PASSWORD_CHANGE, events.OUTCOME_SUCCESS,
           detail={"action": "invitation_accepted"})
    return _start_session(db, request, response, user)


# ── Session lifecycle ─────────────────────────────────────────────────────────
@router.post("/refresh")
@limiter.limit("30/minute")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    require_same_origin(request)
    token = request.cookies.get(BA.COOKIE, "")
    row = (
        db.execute(
            select(AuthSession).where(
                AuthSession.refresh_hash == sessions.hash_refresh(token),
            )
        ).scalar_one_or_none()
        if token
        else None
    )
    if row is None or row.subject_type != BA.SUBJECT or row.client_id is not None:
        raise HTTPException(401, BA.SESSION_ENDED)
    user = BA.active_user(db, row.subject_id, lock=True)
    # Checked before rotating, so another firm's cookie (or a session whose
    # sign-in method was switched off) is refused unspent.
    policy = BA.staff_policy(db, user)
    if not BA.bound_to_firm(user, row.broker_firm_id, request_firm(request)) or not (
        policy.method_enabled
    ):
        raise HTTPException(401, BA.SESSION_ENDED)
    result = sessions.rotate_session(
        db, token, absolute_hours=BA.ABSOLUTE_HOURS, idle_minutes=BA.IDLE_MINUTES
    )
    if result.reuse_detected:
        _event(db, request, user, events.EVENT_TOKEN_REUSE, events.OUTCOME_BLOCKED)
    elif result.session is not None:
        _event(db, request, user, events.EVENT_TOKEN_REFRESH, events.OUTCOME_SUCCESS)
    db.commit()  # Persist revocations even when rotation is rejected.
    if result.session is None:
        raise HTTPException(401, BA.SESSION_ENDED)
    child = db.get(AuthSession, result.session.session_id)
    assert child is not None
    BA.set_cookie(response, result.session.token, result.session.expires_at)
    return BA.response_session(user, child, mfa_required=policy.authenticator_required)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    require_same_origin(request)
    claims = BA.decode(BA.bearer(request.headers.get("authorization")), logout=True)
    row = db.get(AuthSession, claims["sid"])
    if (
        row is None
        or row.subject_type != BA.SUBJECT
        or row.subject_id != claims["sub"]
        or row.client_id is not None
    ):
        raise HTTPException(401, "Invalid sign-out session.")
    user = db.get(User, row.subject_id)
    if user is not None and not BA.bound_to_firm(user, row.broker_firm_id, request_firm(request)):
        raise HTTPException(401, "Invalid sign-out session.")
    cookie = request.cookies.get(BA.COOKIE)
    cookie_row = (
        db.execute(
            select(AuthSession).where(
                AuthSession.refresh_hash == sessions.hash_refresh(cookie),
            )
        ).scalar_one_or_none()
        if cookie
        else None
    )
    if cookie_row is not None and cookie_row.family_id == row.family_id:
        BA.clear_cookie(response)
    if row.revoked_at is None:
        sessions.revoke_family(db, row.family_id)
        if user is not None:
            _event(db, request, user, events.EVENT_LOGOUT, events.OUTCOME_SUCCESS)
        db.commit()
    response.headers["Cache-Control"] = "no-store"


# ── Authenticator ─────────────────────────────────────────────────────────────
@router.get("/mfa")
def mfa_status(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user, row, required = _auth(request, db)
    response.headers["Cache-Control"] = "no-store"
    return {"status": mfa.status_for(db, BA.SUBJECT, user.id), "verified": row.mfa_verified,
            "required": required}


@router.post("/mfa/start")
@limiter.limit("5/minute")
def mfa_start(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, str]:
    user, _, required = _auth(request, db)
    _require_mfa(required)
    issuer = resolve_staff_brand(db, user.broker_firm_id).product_name
    secret, uri = mfa.start_enrollment(db, BA.SUBJECT, user.id, user.email, issuer)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"secret": secret, "otpauth_uri": uri}


def _complete(
    db: Session, request: Request, response: Response, user: User, row: AuthSession
) -> None:
    root = db.execute(
        select(AuthSession).where(
            AuthSession.family_id == row.family_id,
            AuthSession.parent_id.is_(None),
        )
    ).scalar_one()
    start = root.issued_at.replace(tzinfo=UTC) if root.issued_at.tzinfo is None else root.issued_at
    sessions.confirm_session_mfa(db, row.id)
    db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == row.family_id)
        .values(
            expires_at=start + timedelta(hours=BA.ABSOLUTE_HOURS),
        )
    )
    _event(db, request, user, events.EVENT_MFA_SUCCESS, events.OUTCOME_SUCCESS)
    _event(db, request, user, events.EVENT_LOGIN_SUCCESS, events.OUTCOME_SUCCESS)
    db.commit()
    cookie = request.cookies.get(BA.COOKIE)
    cookie_row = (
        db.execute(
            select(AuthSession).where(
                AuthSession.refresh_hash == sessions.hash_refresh(cookie),
            )
        ).scalar_one_or_none()
        if cookie
        else None
    )
    if (
        cookie_row is not None
        and cookie_row.family_id == row.family_id
        and cookie
        and cookie_row.rotated_at is None
        and cookie_row.revoked_at is None
    ):
        BA.set_cookie(response, cookie, start + timedelta(hours=BA.ABSOLUTE_HOURS))


@router.post("/mfa/confirm")
@limiter.shared_limit("5/15minutes", scope="broker-mfa", key_func=_account_bucket)
def mfa_confirm(
    body: Code, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user, row, required = _auth(request, db)
    _require_mfa(required)
    codes = mfa.confirm_enrollment(db, BA.SUBJECT, user.id, body.code)
    if codes is None:
        _event(db, request, user, events.EVENT_MFA_FAIL, events.OUTCOME_FAIL)
        db.commit()
        raise HTTPException(401, _CODE_MISMATCH)
    _complete(db, request, response, user, row)
    response.headers["Cache-Control"] = "no-store"
    return {**BA.response_session(user, row, mfa_required=required), "recovery_codes": codes}


@router.post("/mfa/verify")
@limiter.shared_limit("5/15minutes", scope="broker-mfa", key_func=_account_bucket)
def mfa_verify(
    body: Code, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user, row, required = _auth(request, db)
    _require_mfa(required)
    if row.mfa_verified:
        raise HTTPException(409, "This session is already verified.")
    if not mfa.has_confirmed(db, BA.SUBJECT, user.id) or not (
        mfa.verify_totp(db, BA.SUBJECT, user.id, body.code)
        or mfa.consume_recovery_code(db, BA.SUBJECT, user.id, body.code)
    ):
        _event(db, request, user, events.EVENT_MFA_FAIL, events.OUTCOME_FAIL)
        db.commit()
        raise HTTPException(401, _CODE_MISMATCH)
    _complete(db, request, response, user, row)
    response.headers["Cache-Control"] = "no-store"
    return BA.response_session(user, row, mfa_required=required)
