"""Broker application sessions, separate from Microsoft and HR credentials.

A session is bound to a broker firm: the session row records the user's firm
(`auth_sessions.broker_firm_id`, NULL for the firm-less master admin), and a
request on a host that names a different firm (`tenant_resolution`) is refused.
The binding lives on the row rather than in a token claim, so access tokens
issued before it existed keep working.

Staff sign in with Microsoft (`idp` "entra": the account is bound to a
directory and object id) or with email + password (`idp` "local": the account
has no Microsoft binding and holds an `auth_credentials` row). An account bound
to Microsoft always signs in with Microsoft. Which methods a firm offers is
`core/identity_providers.py`; a method switched off ends its sessions on the
next request.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import jwt
from fastapi import HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import sessions
from app.core.auth import Principal
from app.core.identity_providers import SignInMethods, staff_methods
from app.core.settings import get_settings
from app.core.tenant_resolution import FirmContext, refuse_unserved_surface
from app.models import AuthCredential, AuthSession, User
from app.models.invitation import INVITE_STATUS_ACCEPTED, INVITE_STATUS_PENDING, Invitation
from app.models.platform import DOMAIN_SURFACE_STAFF, IDP_ENTRA, IDP_LOCAL
from app.models.user import USER_STATUS_INVITED

SUBJECT = "broker"
COOKIE = "inspro_broker_refresh"
COOKIE_PATH = "/api/v1/broker/auth"
IDLE_MINUTES = 30
ABSOLUTE_HOURS = 12
# A session that still owes its second factor lasts only this long and reaches
# only the `/broker/auth/mfa*` endpoints.
PENDING_MFA_MINUTES = 5
MFA_CHALLENGE_MINUTES = 5
ROLE_SYSTEM_ADMIN = "system_admin"
BROKER_ROLES = frozenset({"broker_admin", "broker_viewer", "firm_admin", ROLE_SYSTEM_ADMIN})
# Roles that may hold a password: firm staff. The master admin signs in with
# the platform directory only.
LOCAL_ROLES = BROKER_ROLES - {ROLE_SYSTEM_ADMIN}
SESSION_ENDED = "Session ended. Sign in again."
_TOKEN_TYPE_MFA = "broker_mfa"
_MFA_KEY_LABEL = b"inspro-broker-mfa-challenge-v1"


class StaffPolicy(NamedTuple):
    """Whether an account's sign-in method is on, and whether it needs the
    platform authenticator."""

    method_enabled: bool
    authenticator_required: bool


class Authenticated(NamedTuple):
    user: User
    session: AuthSession
    authenticator_required: bool


def _derive_key(label: bytes) -> str:
    return hmac.new(get_settings().portal_jwt_secret.encode(), label, "sha256").hexdigest()


def signing_key() -> str:
    return _derive_key(b"inspro-broker-access-v1")


def _ended() -> HTTPException:
    return HTTPException(403, {"code": "no_access", "message": "Account access has ended."})


def invitation_invalid() -> HTTPException:
    return HTTPException(403, {
        "code": "invitation_invalid",
        "message": "This invitation link is no longer valid. Ask your administrator for a new one.",
    })


def sign_in_method_disabled(message: str) -> HTTPException:
    return HTTPException(403, {"code": "sign_in_method_disabled", "message": message})


def decode(token: str, *, logout: bool = False) -> dict[str, Any]:
    """A broker access token's claims, or 401.

    Tokens without `idp` predate password sign-in and are Microsoft tokens.
    A Microsoft token names the account's object id (`oid`); a local one has none.
    """
    try:
        claims = jwt.decode(
            token,
            signing_key(),
            algorithms=["HS256"],
            options={
                "verify_exp": not logout,
                "require": ["sub", "sid", "iat", "exp", "typ"],
            },
        )
        idp = claims.get("idp", IDP_ENTRA)
        required = ("sub", "sid") if idp == IDP_LOCAL else ("sub", "sid", "oid")
        if (
            claims["typ"] != SUBJECT
            or idp not in (IDP_ENTRA, IDP_LOCAL)
            or (idp == IDP_LOCAL and claims.get("oid") is not None)
            or not all(isinstance(claims.get(name), str) and claims[name] for name in required)
        ):
            raise jwt.InvalidTokenError("wrong broker token")
        return claims
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, SESSION_ENDED) from exc


def bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    return token


def user_idp(user: User) -> str:
    """How the account signs in: Microsoft when it is bound to an identity."""
    return IDP_ENTRA if user.external_id else IDP_LOCAL


def credential(db: Session, user_id: str) -> AuthCredential | None:
    return db.execute(
        select(AuthCredential).where(AuthCredential.user_id == user_id)
    ).scalar_one_or_none()


def active_user(
    db: Session, user_id: str, oid: str | None = None, *, lock: bool = False,
    idp: str | None = None,
) -> User:
    """The active staff account behind a session, or 403 `no_access`.

    `idp` is the method the session was opened with (None: the account's own).
    A Microsoft session needs the bound object id (and `oid` to match it); a
    local one needs an unbound firm-staff account that holds a password.
    """
    # Cookie writers serialize with administrative policy edits and revocation.
    user = (db.execute(
        select(User).where(User.id == user_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none() if lock else db.get(User, user_id))
    if user is None or user.status != "active" or user.role not in BROKER_ROLES:
        raise _ended()
    if (idp or user_idp(user)) == IDP_LOCAL:
        if (
            user.external_id is not None
            or user.role not in LOCAL_ROLES
            or credential(db, user.id) is None
        ):
            raise _ended()
    elif not user.external_id or (oid is not None and user.external_id != oid):
        raise _ended()
    return user


def session_firm(user: User) -> str | None:
    """The firm a new session for `user` records: none for the master admin."""
    return None if user.role == ROLE_SYSTEM_ADMIN else user.broker_firm_id


def bound_to_firm(user: User, row_firm_id: str | None, firm: FirmContext | None) -> bool:
    """Whether a session recording `row_firm_id` may be used on this request's host.

    The master admin works only on platform hosts; everyone else only on their
    own firm's hosts. Both the session's recorded firm and the user's current
    one must match, so moving a user to another firm ends sessions opened under
    the old one. No firm context (local tools, tests) binds nothing.
    """
    if firm is None:
        return True
    if user.role == ROLE_SYSTEM_ADMIN:
        return firm.is_platform_host and row_firm_id is None
    return user.broker_firm_id == firm.firm_id and row_firm_id == firm.firm_id


def account_methods(db: Session, user: User) -> SignInMethods:
    return staff_methods(db, user.broker_firm_id, user.role)


def staff_policy(db: Session, user: User) -> StaffPolicy:
    """Whether the account's method is on, and whether it needs the authenticator.

    Password accounts always need it. Microsoft accounts need it when an
    administrator required it for the account or the firm requires it for
    Microsoft sign-in.
    """
    methods = account_methods(db, user)
    if user_idp(user) == IDP_LOCAL:
        return StaffPolicy(methods.local, True)
    entra = methods.entra
    return StaffPolicy(
        entra is not None,
        user.broker_mfa_required or bool(entra and entra.require_platform_mfa),
    )


def authenticate(
    authorization: str | None, db: Session, *, lock_user: bool = False,
    firm: FirmContext | None = None,
) -> Authenticated:
    claims = decode(bearer(authorization))
    user = active_user(
        db, claims["sub"], claims.get("oid"), lock=lock_user, idp=claims.get("idp", IDP_ENTRA)
    )
    # Refused before validation, which would otherwise extend the idle window.
    issued = db.get(AuthSession, claims["sid"])
    if issued is not None and not bound_to_firm(user, issued.broker_firm_id, firm):
        raise HTTPException(401, SESSION_ENDED)
    policy = staff_policy(db, user)
    if not policy.method_enabled:
        raise HTTPException(401, SESSION_ENDED)
    row = sessions.validate_access_session(
        db,
        claims["sid"],
        subject_type=SUBJECT,
        subject_id=user.id,
        client_id=None,
        idle_minutes=IDLE_MINUTES,
    )
    return Authenticated(user, row, policy.authenticator_required)


def broker_principal(
    authorization: str | None, db: Session, *, firm: FirmContext | None = None,
) -> Principal:
    """The broker user behind a bearer token, on a host that serves staff.

    `firm` is the request's `FirmContext` (`tenant_resolution.request_firm`);
    with it the session must belong to that firm (see `bound_to_firm`) and a
    host that serves only the client portals answers 404.
    """
    refuse_unserved_surface(firm, DOMAIN_SURFACE_STAFF)
    user, row, authenticator_required = authenticate(authorization, db, firm=firm)
    if authenticator_required and not row.mfa_verified:
        raise HTTPException(
            403, {"code": "broker_mfa_required", "message": "Complete two-factor verification."}
        )
    return Principal(user.id, user.broker_firm_id, user.role, user.email)  # type: ignore[arg-type]


def response_session(user: User, row: AuthSession, *, mfa_required: bool) -> dict[str, Any]:
    now = datetime.now(UTC)
    absolute = (
        row.expires_at.replace(tzinfo=UTC) if row.expires_at.tzinfo is None else row.expires_at
    )
    expiry = min(now + timedelta(minutes=10), absolute)
    idp = user_idp(user)
    claims: dict[str, Any] = {
        "sub": user.id,
        "sid": row.id,
        "idp": idp,
        "typ": SUBJECT,
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }
    if idp == IDP_ENTRA:
        claims["oid"] = user.external_id
    token = jwt.encode(claims, signing_key(), algorithm="HS256")
    return {
        "access_token": token,
        "expires_at": expiry.isoformat(),
        "user": {"id": user.id, "email": user.email, "display_name": user.display_name},
        "mfa_verified": row.mfa_verified,
        "mfa_required": mfa_required,
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


# ── Password sign-in ──────────────────────────────────────────────────────────
def local_account(
    db: Session, firm_id: str, *, email: str | None = None, user_id: str | None = None,
) -> tuple[User, AuthCredential] | None:
    """A firm-staff account that signs in with a password, in `firm_id`.

    Found by email (sign-in) or id (second step). Accounts bound to Microsoft,
    HR users and the master admin are never found.
    """
    stmt = select(User).where(
        User.broker_firm_id == firm_id,
        User.role.in_(LOCAL_ROLES),
        User.external_id.is_(None),
    )
    stmt = stmt.where(User.email == email) if email is not None else stmt.where(User.id == user_id)
    user = db.execute(stmt).scalar_one_or_none()
    cred = credential(db, user.id) if user is not None else None
    return (user, cred) if user is not None and cred is not None else None


def issue_mfa_challenge(user_id: str, firm_id: str) -> str:
    """The token bridging a correct password to the second-factor step."""
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "fid": firm_id,
            "typ": _TOKEN_TYPE_MFA,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=MFA_CHALLENGE_MINUTES)).timestamp()),
        },
        _derive_key(_MFA_KEY_LABEL),
        algorithm="HS256",
    )


def verify_mfa_challenge(token: str) -> tuple[str, str]:
    """(user id, firm id) from a challenge token; raises `jwt.InvalidTokenError`."""
    claims = jwt.decode(
        token,
        _derive_key(_MFA_KEY_LABEL),
        algorithms=["HS256"],
        options={"require": ["sub", "fid", "exp", "typ"]},
    )
    if claims["typ"] != _TOKEN_TYPE_MFA:
        raise jwt.InvalidTokenError("wrong token type")
    return str(claims["sub"]), str(claims["fid"])


# ── Staff invitations ─────────────────────────────────────────────────────────
def hash_invite_token(raw: str) -> str:
    """What `invitations.token` stores: the SHA-256 hex of the raw link token."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _unexpired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    return (expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at) > now


def pending_staff_invitation(
    db: Session, raw_token: str, firm: FirmContext | None, *, roles: frozenset[str],
) -> tuple[Invitation, User]:
    """The live invitation behind a link token and the account it provisioned,
    or 403 `invitation_invalid`.

    Pending, unexpired, for one of `roles`, issued for this host's firm (a
    platform-admin invitation: a platform host), and its account still invited.
    The invitation row is locked until the caller commits.
    """
    invitation = db.execute(
        select(Invitation).where(Invitation.token == hash_invite_token(raw_token))
        .with_for_update()
    ).scalar_one_or_none()
    if (
        invitation is None
        or invitation.status != INVITE_STATUS_PENDING
        or invitation.role not in roles
        or not _unexpired(invitation.expires_at, datetime.now(UTC))
    ):
        raise invitation_invalid()
    platform_admin = invitation.role == ROLE_SYSTEM_ADMIN
    if firm is not None and not (
        firm.is_platform_host if platform_admin else firm.firm_id == invitation.broker_firm_id
    ):
        raise invitation_invalid()
    scope = (
        User.broker_firm_id.is_(None) if platform_admin
        else User.broker_firm_id == invitation.broker_firm_id
    )
    user = db.execute(
        select(User).where(User.email == invitation.email, scope)
    ).scalar_one_or_none()
    if user is None or user.status != USER_STATUS_INVITED or user.role not in roles:
        raise invitation_invalid()
    return invitation, user


def consume_invitation(db: Session, invitation: Invitation) -> None:
    """Mark the invitation accepted, once: a concurrent redemption gets 403. No commit."""
    claimed = db.execute(
        update(Invitation)
        .where(Invitation.id == invitation.id, Invitation.status == INVITE_STATUS_PENDING)
        .values(status=INVITE_STATUS_ACCEPTED, accepted_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if getattr(claimed, "rowcount", 0) != 1:
        raise invitation_invalid()
