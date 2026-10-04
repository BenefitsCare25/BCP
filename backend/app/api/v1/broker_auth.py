"""Microsoft token exchange, per-account broker MFA and revocable sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import auth_events as events
from app.core import broker_auth as BA
from app.core import mfa, sessions
from app.core.auth import _entra_principal
from app.core.cookie_auth import require_same_origin
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.session import get_db
from app.models import AuthSession, User

router = APIRouter(prefix="/broker/auth", tags=["broker authentication"])


class Exchange(BaseModel):
    access_token: str = Field(min_length=1, max_length=16384)


class Code(BaseModel):
    code: str = Field(pattern=r"^(?:[0-9]{6}|[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4})$")


def _auth(request: Request, db: Session) -> tuple[User, AuthSession]:
    return BA.authenticate(request.headers.get("authorization"), db, lock_user=True)


def _require_mfa(user: User) -> None:
    if not user.broker_mfa_required:
        raise HTTPException(
            409, "Your administrator has not required an authenticator for this account."
        )


def _account_bucket(request: Request) -> str:
    # A signed subject controls the account bucket; rotating IPs/sessions do not.
    try:
        claims = BA.decode(BA.bearer(request.headers.get("authorization")))
        return "broker-mfa:" + str(claims["sub"])
    except HTTPException:
        return "broker-mfa-invalid:" + (request.client.host if request.client else "unknown")


def _event(db: Session, request: Request, user: User, event: str, outcome: str) -> None:
    events.write_auth_event(
        db,
        surface="broker",
        subject_type=BA.SUBJECT,
        subject_id=user.id,
        broker_firm_id=user.broker_firm_id,
        event_type=event,
        outcome=outcome,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/exchange")
@limiter.limit("10/minute")
def exchange(
    body: Exchange, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    require_same_origin(request)
    if get_settings().auth_mode != "entra":
        raise HTTPException(404, "Microsoft sign-in is not enabled.")
    try:
        principal = _entra_principal("Bearer " + body.access_token, db)
    except HTTPException:
        events.write_auth_event(
            db,
            surface="broker",
            event_type=events.EVENT_LOGIN_FAIL,
            outcome=events.OUTCOME_FAIL,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        db.commit()
        raise
    user = BA.active_user(db, principal.user_id, lock=True)
    # Required accounts get only a five-minute enrollment/verification session.
    issued = sessions.issue_session(
        db,
        subject_type=BA.SUBJECT,
        subject_id=user.id,
        client_id=None,
        broker_firm_id=user.broker_firm_id,
        absolute_hours=BA.ABSOLUTE_HOURS,
        expires_at=(datetime.now(UTC) + timedelta(minutes=5)
                    if user.broker_mfa_required else None),
    )
    row = db.get(AuthSession, issued.session_id)
    assert row is not None
    _event(db, request, user, events.EVENT_MFA_CHALLENGE if user.broker_mfa_required
           else events.EVENT_LOGIN_SUCCESS, events.OUTCOME_SUCCESS)
    db.commit()
    BA.set_cookie(response, issued.token, issued.expires_at)
    return BA.response_session(user, row)


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
        raise HTTPException(401, "Session ended. Sign in again.")
    user = BA.active_user(db, row.subject_id, lock=True)
    result = sessions.rotate_session(
        db, token, absolute_hours=BA.ABSOLUTE_HOURS, idle_minutes=BA.IDLE_MINUTES
    )
    if result.reuse_detected:
        _event(db, request, user, events.EVENT_TOKEN_REUSE, events.OUTCOME_BLOCKED)
    elif result.session is not None:
        _event(db, request, user, events.EVENT_TOKEN_REFRESH, events.OUTCOME_SUCCESS)
    db.commit()  # Persist revocations even when rotation is rejected.
    if result.session is None:
        raise HTTPException(401, "Session ended. Sign in again.")
    child = db.get(AuthSession, result.session.session_id)
    assert child is not None
    BA.set_cookie(response, result.session.token, result.session.expires_at)
    return BA.response_session(user, child)


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
        user = db.get(User, row.subject_id)
        if user is not None:
            _event(db, request, user, events.EVENT_LOGOUT, events.OUTCOME_SUCCESS)
        db.commit()
    response.headers["Cache-Control"] = "no-store"


@router.get("/mfa")
def mfa_status(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user, row = _auth(request, db)
    response.headers["Cache-Control"] = "no-store"
    return {"status": mfa.status_for(db, BA.SUBJECT, user.id), "verified": row.mfa_verified,
            "required": user.broker_mfa_required}


@router.post("/mfa/start")
@limiter.limit("5/minute")
def mfa_start(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, str]:
    user, _ = _auth(request, db)
    _require_mfa(user)
    secret, uri = mfa.start_enrollment(db, BA.SUBJECT, user.id, user.email)
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
    user, row = _auth(request, db)
    _require_mfa(user)
    codes = mfa.confirm_enrollment(db, BA.SUBJECT, user.id, body.code)
    if codes is None:
        _event(db, request, user, events.EVENT_MFA_FAIL, events.OUTCOME_FAIL)
        db.commit()
        raise HTTPException(401, "That code did not match. Try again.")
    _complete(db, request, response, user, row)
    response.headers["Cache-Control"] = "no-store"
    return {**BA.response_session(user, row), "recovery_codes": codes}


@router.post("/mfa/verify")
@limiter.shared_limit("5/15minutes", scope="broker-mfa", key_func=_account_bucket)
def mfa_verify(
    body: Code, request: Request, response: Response, db: Session = Depends(get_db)
) -> dict[str, Any]:
    user, row = _auth(request, db)
    _require_mfa(user)
    if row.mfa_verified:
        raise HTTPException(409, "This session is already verified.")
    if not mfa.has_confirmed(db, BA.SUBJECT, user.id) or not (
        mfa.verify_totp(db, BA.SUBJECT, user.id, body.code)
        or mfa.consume_recovery_code(db, BA.SUBJECT, user.id, body.code)
    ):
        _event(db, request, user, events.EVENT_MFA_FAIL, events.OUTCOME_FAIL)
        db.commit()
        raise HTTPException(401, "That code did not match. Try again.")
    _complete(db, request, response, user, row)
    response.headers["Cache-Control"] = "no-store"
    return BA.response_session(user, row)
