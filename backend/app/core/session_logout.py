"""Revoke the requesting tab's family without touching another tab's cookie."""
from typing import Literal

import jwt
from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import hr_auth, sessions
from app.core.settings import get_settings
from app.models import AuthSession
from app.models.auth import SUBJECT_MEMBER, SUBJECT_USER


def revoke_tab_session(
    db: Session, request: Request, *, surface: Literal["portal", "hr"],
    client_id: str, cookie_name: str,
) -> tuple[AuthSession | None, bool]:
    """Return the newly revoked row and whether the cookie belongs to its family.

    An expired access token may identify a session for revocation ONLY. Refreshing
    first could silently select another account's shared cookie. Signature, token
    type, subject, session and tenant checks still apply; there is no cookie-only
    fallback. Revoked sessions remain safe to sign out again.
    """
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid sign-out session.")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise invalid
    settings = get_settings()
    key = (hr_auth._derive_key(settings, hr_auth._HR_KEY_LABEL)
           if surface == "hr" else settings.portal_jwt_secret)
    tenant_claim = "cid" if surface == "hr" else "client_id"
    try:
        claims = jwt.decode(token, key, algorithms=["HS256"], options={
            "verify_exp": False, "require": ["sub", "sid", "exp", tenant_claim],
        })
    except jwt.InvalidTokenError as exc:
        raise invalid from exc
    if (
        claims.get("typ") != ("hr" if surface == "hr" else "member")
        or claims[tenant_claim] != client_id
        or not isinstance(claims["sid"], str)
    ):
        raise invalid
    row = db.get(AuthSession, claims["sid"])
    if (
        row is None or row.subject_id != claims["sub"] or row.client_id != client_id
        or row.subject_type != (SUBJECT_USER if surface == "hr" else SUBJECT_MEMBER)
    ):
        raise invalid
    cookie = request.cookies.get(cookie_name)
    cookie_row = db.execute(select(AuthSession).where(
        AuthSession.refresh_hash == sessions.hash_refresh(cookie),
    )).scalar_one_or_none() if cookie else None
    clear_cookie = cookie_row is not None and cookie_row.family_id == row.family_id
    if row.revoked_at is not None:
        return None, clear_cookie
    sessions.revoke_family(db, row.family_id)
    return row, clear_cookie
