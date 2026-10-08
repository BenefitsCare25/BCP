"""Auth dependency seam.

Two modes selected by `INSPRO_AUTH_MODE`:

- `mock` (default, development only): a fixed `CurrentUser` for the demo
  client, unless the request carries a broker session token (password sign-in
  works in development), which then authenticates exactly as in Entra mode.
- `entra`: every request carries an Inspro broker session token
  (`core/broker_auth.py`), opened by exchanging a Microsoft token
  (`_entra_principal`) or by password sign-in.

Every API route depends on `get_current_user`. The contract is the same in
both modes, so swapping is a config change — no code change needed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, get_args

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.entra import EntraAuthError, verify_entra_token
from app.core.identity import (
    PlatformAccess,
    platform_access_for_client,
    resolve_active_client_id,
)
from app.core.settings import get_settings
from app.core.tenant_resolution import FirmContext, request_firm
from app.db.session import get_db
from app.db.tenancy import set_search_path

if TYPE_CHECKING:
    from app.models import User

logger = logging.getLogger(__name__)

Role = Literal[
    "broker_admin", "broker_viewer", "client_admin", "client_hr", "firm_admin", "system_admin"
]
VALID_ROLES: frozenset[str] = frozenset(get_args(Role))
ROLE_SYSTEM_ADMIN = "system_admin"
ROLE_FIRM_ADMIN = "firm_admin"
ROLE_BROKER_VIEWER = "broker_viewer"
# Who may manage a firm's users and use destructive actions inside it: the
# firm's own administrators, and the platform master admin.
FIRM_OWNER_ROLES: frozenset[str] = frozenset({ROLE_FIRM_ADMIN, ROLE_SYSTEM_ADMIN})
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def client_selection_stale() -> HTTPException:
    """409 for a request whose selected company is not the one it acts on.

    A fresh exception per raise: the frontend branches on `code`, and a shared
    instance would carry state from one request into the next.
    """
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "client_selection_stale",
            "message": "The selected company is no longer available. "
            "Refresh and choose a company.",
        },
    )


def client_selection_required() -> HTTPException:
    """409 for a platform admin's company-scoped write with no company chosen."""
    return HTTPException(
        status.HTTP_409_CONFLICT,
        {"code": "client_selection_required", "message": "Choose a company first."},
    )


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    broker_firm_id: str | None
    client_id: str | None
    role: Role
    email: str | None = None
    # system_admin only, for the active company's firm (None with no company).
    platform_access: PlatformAccess | None = None


@dataclass(frozen=True)
class Principal:
    """An authenticated identity before an active client is selected."""

    user_id: str
    broker_firm_id: str | None
    role: Role
    email: str | None = None


# Seed values for the mock path — kept in sync with scripts/seed_demo.py.
DEMO_USER_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USER_EMAIL = "demo.broker@inspro.test"
DEMO_BROKER_FIRM_ID = "00000000-0000-0000-0000-000000000010"
DEMO_CLIENT_ID = "00000000-0000-0000-0000-000000000020"


def _mock_role() -> Role:
    """Dev-only: let `INSPRO_MOCK_ROLE` pick the mock user's role so role-gated
    surfaces (e.g. the system-admin platform-AI settings) can be exercised
    locally. Only ever consulted in mock auth mode; unknown values fall back to
    broker_admin."""
    raw = os.environ.get("INSPRO_MOCK_ROLE", "").strip()
    return raw if raw in VALID_ROLES else "broker_admin"  # type: ignore[return-value]


def _mock_user() -> CurrentUser:
    return CurrentUser(
        user_id=DEMO_USER_ID,
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        client_id=DEMO_CLIENT_ID,
        role=_mock_role(),
    )


def _mock_principal() -> Principal:
    return Principal(
        user_id=DEMO_USER_ID,
        broker_firm_id=DEMO_BROKER_FIRM_ID,
        role=_mock_role(),
    )


def _activate_invited_user(db: Session, user: User) -> None:
    """Accept the user's live invitation on first sign-in, or refuse with 403.

    Invitations are matched by email within the user's firm. A platform admin
    belongs to no firm, while its invitation row still records the firm it was
    issued from (the column is NOT NULL), so it is matched by email and role.
    """
    from app.models.invitation import INVITE_STATUS_ACCEPTED, INVITE_STATUS_PENDING, Invitation
    from app.models.user import USER_STATUS_ACTIVE

    scope = (
        Invitation.role == ROLE_SYSTEM_ADMIN
        if user.role == ROLE_SYSTEM_ADMIN
        else Invitation.broker_firm_id == user.broker_firm_id
    )
    pending = db.query(Invitation).filter(
        Invitation.email == user.email,
        scope,
        Invitation.status == INVITE_STATUS_PENDING,
    ).all()
    now = datetime.now(UTC)
    valid_pending = [inv for inv in pending if inv.expires_at is not None and (
        inv.expires_at.replace(tzinfo=UTC) if inv.expires_at.tzinfo is None
        else inv.expires_at
    ) > now]
    if not valid_pending:
        raise HTTPException(status.HTTP_403_FORBIDDEN, {
            "code": "invitation_expired",
            "message": "Invitation expired. Contact your administrator.",
        })
    user.status = USER_STATUS_ACTIVE
    for inv in valid_pending:
        inv.status = INVITE_STATUS_ACCEPTED
        inv.accepted_at = now
    db.commit()


def _bound_user(db: Session, tid: str, oid: str) -> User | None:
    """The account bound to Microsoft identity (`tid`, `oid`).

    An object id is unique only within its directory, so the binding key is
    both. A row bound before directories were recorded (`external_tid` NULL)
    belongs to the platform directory: it is accepted only for a token from
    that directory, only for the master admin or the platform owner's firm,
    and is stamped with the directory on first use.
    """
    from sqlalchemy import select

    from app.core.tenant_resolution import platform_owner_firm
    from app.models import User  # lazy: avoid import cost at module load

    user = db.execute(
        select(User).where(User.external_tid == tid, User.external_id == oid)
    ).scalar_one_or_none()
    if user is not None:
        return user
    legacy = db.execute(
        select(User).where(User.external_tid.is_(None), User.external_id == oid)
    ).scalar_one_or_none()
    if legacy is None or tid != get_settings().entra_tenant_id:
        return None
    if legacy.role != ROLE_SYSTEM_ADMIN:
        owner = platform_owner_firm(db)
        if owner is None or legacy.broker_firm_id != owner.id:
            return None
    legacy.external_tid = tid
    db.flush()
    return legacy


def _redeem_entra_invitation(
    db: Session, invite_token: str, firm: FirmContext | None, *, tid: str, oid: str
) -> User:
    """Bind an unbound Microsoft identity to the account an invitation link
    provisioned, activate it and consume the invitation (single use), or 403
    `invitation_invalid`. Commits nothing."""
    from sqlalchemy.exc import IntegrityError

    from app.core import broker_auth as BA  # lazy: imports this module
    from app.models.user import USER_STATUS_ACTIVE

    invitation, user = BA.pending_staff_invitation(
        db, invite_token, firm, roles=BA.BROKER_ROLES
    )
    if user.external_id is not None or BA.credential(db, user.id) is not None:
        raise BA.invitation_invalid()
    BA.consume_invitation(db, invitation)
    user.external_tid, user.external_id = tid, oid
    user.status = USER_STATUS_ACTIVE
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise BA.invitation_invalid() from None
    return user


def _verified_identity(token: str, db: Session, firm: FirmContext | None) -> tuple[str, str]:
    """(`tid`, `oid`) of a Microsoft token from the host firm's own directory.

    403 `sign_in_method_disabled` when the host's firm does not offer Microsoft
    sign-in; 401 for any token the directory did not issue to this app.
    """
    from app.core.broker_auth import sign_in_method_disabled
    from app.core.identity_providers import host_directory

    directory = host_directory(db, firm)
    if directory is None:
        raise sign_in_method_disabled("Microsoft sign-in is not enabled for this organisation.")
    try:
        claims = verify_entra_token(token, get_settings(), tenant_id=directory.tenant_id)
    except EntraAuthError as exc:
        logger.warning("Entra token rejected")
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid Entra token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    oid = claims.get("oid")
    if not oid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing user identifier")
    return directory.tenant_id, str(oid)


def _entra_principal(
    authorization: str | None,
    db: Session,
    *,
    firm: FirmContext | None = None,
    invite_token: str | None = None,
) -> Principal:
    """Verify the Bearer token against the host firm's directory and resolve
    the DB-backed user.

    Only an account bound to the token's (directory, object id) grants access.
    Email claims never bind identities or determine authorization. An unbound
    identity is bound only by redeeming an invitation link (`invite_token`).

    ``firm`` is the request's host firm (`tenant_resolution.request_firm`). An
    invitation is accepted only where the account may sign in
    (`broker_auth.bound_to_firm`): its own firm's hosts, or a platform host for
    the master admin. Elsewhere it stays pending and the sign-in is refused.
    """
    from app.core.broker_auth import bound_to_firm, session_firm  # lazy: imports this module
    from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_INVITED

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tid, oid = _verified_identity(authorization.removeprefix("Bearer ").strip(), db, firm)
    user = _bound_user(db, tid, oid)
    if user is None and invite_token:
        user = _redeem_entra_invitation(db, invite_token, firm, tid=tid, oid=oid)
    if (
        user is not None
        and user.status == USER_STATUS_INVITED
        and bound_to_firm(user, session_firm(user), firm)
    ):
        _activate_invited_user(db, user)

    if user is None or user.status != USER_STATUS_ACTIVE:
        logger.warning("Entra identity has no active provisioned account")
        # Coded detail, not a bare string: this is the IDENTITY-level 403 (the
        # person authenticated with Microsoft but no Inspro user row grants them
        # access), which must end the session — unlike a permission 403 on one
        # endpoint. The frontend branches on `code`, never on the message.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {
                "code": "no_access",
                "message": "User has no access — contact your administrator.",
            },
        )

    role_str = user.role if user.role in VALID_ROLES else ROLE_BROKER_VIEWER
    role: Role = role_str  # type: ignore[assignment]
    return Principal(
        user_id=user.id,
        broker_firm_id=user.broker_firm_id,
        role=role,
        email=user.email,
    )


def _build_current_user(
    principal: Principal, requested_client_id: str | None, db: Session, *, read_only: bool
) -> CurrentUser:
    """Resolve the active company for one request.

    `read_only` is True for GET/HEAD/OPTIONS. A selected company the caller can
    no longer reach is replaced by their default only on reads; a write naming
    one is refused. With no selection, firm roles default to their first
    company (single-company callers send no header), but a system_admin write
    gets none, since its "first company" is arbitrary across every firm.
    """
    if not requested_client_id and principal.role == ROLE_SYSTEM_ADMIN and not read_only:
        # A platform admin reaches several firms (its own standing access plus
        # any granted), so "the first accessible client" is an arbitrary
        # company. Leave it unset: endpoints that act on a company refuse with
        # `client_selection_required`, while platform surfaces (firm
        # management, platform AI settings) need none.
        active = None
    else:
        active = resolve_active_client_id(
            role=principal.role,
            broker_firm_id=principal.broker_firm_id,
            user_id=principal.user_id,
            requested_client_id=requested_client_id,
            db=db,
        )
    if requested_client_id and active is None:
        if not read_only:
            # A write aimed at a company the caller can no longer reach (stale
            # tab, revoked access, another user's stored selection) must not
            # land on whichever company the fallback below would pick.
            logger.info(
                "requested client %s not accessible for user %s; refusing the write",
                requested_client_id, principal.user_id,
            )
            raise client_selection_stale()
        # Reads fall back to the caller's default client instead of failing —
        # a hard error here (including on /me) would lock the user out with no
        # way to recover. The frontend reconciles its stored selection from the
        # active_client_id we return.
        logger.info(
            "requested client %s not accessible for user %s; using default",
            requested_client_id, principal.user_id,
        )
        active = resolve_active_client_id(
            role=principal.role,
            broker_firm_id=principal.broker_firm_id,
            user_id=principal.user_id,
            requested_client_id=None,
            db=db,
        )
    access: PlatformAccess | None = None
    if principal.role == ROLE_SYSTEM_ADMIN and active is not None:
        access = platform_access_for_client(db, principal.user_id, active)
    return CurrentUser(
        user_id=principal.user_id,
        broker_firm_id=principal.broker_firm_id,
        client_id=active,
        role=principal.role,
        email=principal.email,
        platform_access=access,
    )


def _route_to_firm(db: Session, user: CurrentUser) -> None:
    """Bind the request's session to the active company's firm schema.

    A system_admin belongs to no firm, so its schema always comes from the
    active client; with none selected it stays on `public`, where only control
    tables are read. Every other role is pinned to its own firm, and an active
    client from another firm is refused as defence in depth (identity
    resolution should already have rejected it). Routing is a no-op on SQLite.
    """
    from app.models import Client  # lazy

    client = db.get(Client, user.client_id) if user.client_id else None
    if user.role == ROLE_SYSTEM_ADMIN:
        firm_id = client.broker_firm_id if client is not None else None
    else:
        firm_id = user.broker_firm_id
        if client is not None and client.broker_firm_id != firm_id:
            logger.warning(
                "active client %s is outside firm %s for user %s",
                user.client_id, firm_id, user.user_id,
            )
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found")
    set_search_path(db, firm_id)


def _carries_broker_session(authorization: str | None) -> bool:
    """Whether the bearer token is a broker session token at all (signature and
    type; an expired one still counts, so the client is told to refresh)."""
    from app.core.broker_auth import bearer, decode  # lazy: imports this module

    try:
        decode(bearer(authorization), logout=True)
    except HTTPException:
        return False
    return True


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_inspro_client: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    settings = get_settings()
    requested = x_inspro_client.strip() if x_inspro_client else None
    read_only = request.method.upper() in READ_ONLY_METHODS

    # Mock mode (development) serves the demo user, except to a request that
    # carries a broker session token, which is held to every check Entra mode
    # applies; password sign-in works in development that way.
    if settings.auth_mode == "entra" or _carries_broker_session(authorization):
        from app.core.broker_auth import broker_principal

        principal = broker_principal(authorization, db, firm=request_firm(request))
        user = _build_current_user(principal, requested, db, read_only=read_only)
    elif not requested:
        # Mock mode, no explicit client selection: fixed demo user, no DB hit
        # for identity (preserves existing test behaviour).
        user = _mock_user()
    else:
        user = _build_current_user(_mock_principal(), requested, db, read_only=read_only)

    _route_to_firm(db, user)
    return user
