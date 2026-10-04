"""Broker application sessions, separate from Microsoft and HR credentials."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import sessions
from app.core.auth import Principal
from app.core.settings import get_settings
from app.models import AuthSession, User

SUBJECT = "broker"
COOKIE = "inspro_broker_refresh"
COOKIE_PATH = "/api/v1/broker/auth"
IDLE_MINUTES = 30
ABSOLUTE_HOURS = 12
BROKER_ROLES = frozenset({"broker_admin", "broker_viewer", "system_admin"})


def signing_key() -> str:
    return hmac.new(
        get_settings().portal_jwt_secret.encode(), b"inspro-broker-access-v1", "sha256"
    ).hexdigest()


def decode(token: str, *, logout: bool = False) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            signing_key(),
            algorithms=["HS256"],
            options={
                "verify_exp": not logout,
                "require": ["sub", "sid", "oid", "iat", "exp", "typ"],
            },
        )
        if claims["typ"] != SUBJECT or not all(
            isinstance(claims[name], str) and claims[name] for name in ("sub", "sid", "oid")
        ):
            raise jwt.InvalidTokenError("wrong broker token")
        return claims
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Session ended. Sign in again.") from exc


def bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    return token


def active_user(
    db: Session, user_id: str, oid: str | None = None, *, lock: bool = False,
) -> User:
    # Cookie writers serialize with administrative policy edits and revocation.
    user = (db.execute(
        select(User).where(User.id == user_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none() if lock else db.get(User, user_id))
    if (
        user is None
        or user.status != "active"
        or user.role not in BROKER_ROLES
        or not user.external_id
        or (oid is not None and user.external_id != oid)
    ):
        raise HTTPException(403, {"code": "no_access", "message": "Account access has ended."})
    return user


def authenticate(
    authorization: str | None, db: Session, *, lock_user: bool = False,
) -> tuple[User, AuthSession]:
    claims = decode(bearer(authorization))
    user = active_user(db, claims["sub"], claims["oid"], lock=lock_user)
    row = sessions.validate_access_session(
        db,
        claims["sid"],
        subject_type=SUBJECT,
        subject_id=user.id,
        client_id=None,
        idle_minutes=IDLE_MINUTES,
    )
    return user, row


def broker_principal(authorization: str | None, db: Session) -> Principal:
    user, row = authenticate(authorization, db)
    if user.broker_mfa_required and not row.mfa_verified:
        raise HTTPException(
            403, {"code": "broker_mfa_required", "message": "Complete two-factor verification."}
        )
    return Principal(user.id, user.broker_firm_id, user.role, user.email)  # type: ignore[arg-type]


def response_session(user: User, row: AuthSession) -> dict[str, Any]:
    now = datetime.now(UTC)
    absolute = (
        row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    )
    expiry = min(now + timedelta(minutes=10), absolute)
    token = jwt.encode(
        {
            "sub": user.id,
            "sid": row.id,
            "oid": user.external_id,
            "typ": SUBJECT,
            "iat": int(now.timestamp()),
            "exp": int(expiry.timestamp()),
        },
        signing_key(),
        algorithm="HS256",
    )
    return {
        "access_token": token,
        "expires_at": expiry.isoformat(),
        "user": {"id": user.id, "email": user.email, "display_name": user.display_name},
        "mfa_verified": row.mfa_verified,
        "mfa_required": user.broker_mfa_required,
    }


def set_cookie(response: Response, token: str, expiry: datetime) -> None:
    response.set_cookie(
        COOKIE,
        token,
        path=COOKIE_PATH,
        httponly=True,
        secure=get_settings().env != "dev",
        samesite="strict",
        max_age=max(0, int((expiry - datetime.now(UTC)).total_seconds())),
    )
    response.headers["Cache-Control"] = "no-store"


def clear_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=get_settings().env != "dev",
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
