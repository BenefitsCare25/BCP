"""Auth dependency seam.

Two modes selected by `INSPRO_AUTH_MODE`:

- `mock` (default): returns a fixed `CurrentUser` for the demo client. Used in
  local dev, CI, and load tests.
- `entra`: validates a Bearer JWT against the configured Entra tenant's JWKS
  and maps claims to `CurrentUser`.

Every API route depends on `get_current_user`. The contract is the same in
both modes, so swapping is a config change — no code change needed.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, get_args

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.entra import EntraAuthError, verify_entra_token
from app.core.identity import resolve_active_client_id
from app.core.settings import get_settings
from app.db.session import get_db
from app.db.tenancy import set_search_path

logger = logging.getLogger(__name__)

Role = Literal["broker_admin", "broker_viewer", "client_admin", "client_hr", "system_admin"]
VALID_ROLES: frozenset[str] = frozenset(get_args(Role))
ROLE_SYSTEM_ADMIN = "system_admin"
ROLE_BROKER_VIEWER = "broker_viewer"


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    broker_firm_id: str | None
    client_id: str | None
    role: Role
    email: str | None = None


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


def _entra_principal(authorization: str | None, db: Session) -> Principal:
    """Verify the Bearer token and resolve the DB-backed user.

    Tenant binding comes from the `users` table (matched by Entra `oid`, or by
    email on first sign-in), not from custom token claims. An unknown or
    disabled user is refused so a missing binding is visible.
    """
    from app.models import User  # lazy: avoid import cost at module load
    from app.models.invitation import INVITE_STATUS_ACCEPTED, INVITE_STATUS_PENDING, Invitation
    from app.models.user import USER_STATUS_ACTIVE, USER_STATUS_DISABLED, USER_STATUS_INVITED

    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = verify_entra_token(token, settings)
    except EntraAuthError as exc:
        logger.warning("Entra token rejected: %s", exc)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid Entra token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    oid = claims.get("oid") or claims.get("sub")
    if not oid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token missing user identifier")
    oid = str(oid)
    email = claims.get("email") or claims.get("preferred_username")
    email = email.strip().lower() if isinstance(email, str) else None

    user = db.query(User).filter(User.external_id == oid).one_or_none()
    if user is None and email:
        # First sign-in for an invited/provisioned user: link the Entra oid and
        # activate the account, marking any pending invitation accepted. Only an
        # UNLINKED row (external_id is None) may be claimed by email — a row
        # already bound to a different oid must NOT be re-bound, or a reused
        # email could authenticate as someone else's account.
        candidate = db.query(User).filter(User.email == email).one_or_none()
        if candidate is not None and candidate.external_id is None:
            pending = (
                db.query(Invitation)
                .filter(
                    Invitation.email == email,
                    Invitation.broker_firm_id == candidate.broker_firm_id,
                    Invitation.status == INVITE_STATUS_PENDING,
                )
                .all()
            )
            now = datetime.now(UTC)
            valid_pending = [
                inv
                for inv in pending
                if inv.expires_at is None
                or (
                    inv.expires_at.replace(tzinfo=UTC)
                    if inv.expires_at.tzinfo is None
                    else inv.expires_at
                )
                > now
            ]
            if candidate.status == USER_STATUS_INVITED and not valid_pending:
                logger.warning("Expired invitation rejected for email %s", email)
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Invitation has expired. Contact your administrator for a new invitation.",
                )
            candidate.external_id = oid
            if candidate.status == USER_STATUS_INVITED:
                candidate.status = USER_STATUS_ACTIVE
                for inv in valid_pending:
                    inv.status = INVITE_STATUS_ACCEPTED
                    inv.accepted_at = now
            db.commit()
            user = candidate
        elif candidate is not None:
            # Email belongs to an account already linked to a different oid.
            logger.warning(
                "Entra oid %s presented email %s already linked to oid %s — refusing",
                oid, email, candidate.external_id,
            )

    if user is None or user.status == USER_STATUS_DISABLED:
        logger.warning("No active Inspro user for Entra oid %s (email %s)", oid, email)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "User has no access — contact your administrator.",
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
    principal: Principal, requested_client_id: str | None, db: Session
) -> CurrentUser:
    active = resolve_active_client_id(
        role=principal.role,
        broker_firm_id=principal.broker_firm_id,
        user_id=principal.user_id,
        requested_client_id=requested_client_id,
        db=db,
    )
    if requested_client_id and active is None:
        # The selected client isn't accessible (stale/revoked selection, or a
        # client carried over from a previous user on this browser). Fall back
        # to the caller's default client instead of 403'ing every request —
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
    return CurrentUser(
        user_id=principal.user_id,
        broker_firm_id=principal.broker_firm_id,
        client_id=active,
        role=principal.role,
        email=principal.email,
    )


def _route_to_firm(db: Session, user: CurrentUser) -> None:
    """Bind the request's session to the user's firm schema (Postgres only).

    For system_admin (no firm) the schema is derived from the active client.
    No-op on SQLite, so the single-schema dev/test path is unchanged.
    """
    firm_id = user.broker_firm_id
    if firm_id is None and user.client_id:
        from app.models import Client  # lazy

        client = db.get(Client, user.client_id)
        firm_id = client.broker_firm_id if client else None
    set_search_path(db, firm_id)


def get_current_user(
    authorization: str | None = Header(default=None),
    x_inspro_client: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    settings = get_settings()
    requested = x_inspro_client.strip() if x_inspro_client else None

    if settings.auth_mode == "entra":
        principal = _entra_principal(authorization, db)
        user = _build_current_user(principal, requested, db)
    elif not requested:
        # Mock mode, no explicit client selection: fixed demo user, no DB hit
        # for identity (preserves existing test behaviour).
        user = _mock_user()
    else:
        user = _build_current_user(_mock_principal(), requested, db)

    _route_to_firm(db, user)
    return user
