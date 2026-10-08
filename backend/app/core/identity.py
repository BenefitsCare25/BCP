"""Tenant access resolution: which clients a principal may act on, and
validation of the active client selected per request.

Access model (the hard boundary is the broker firm):
- `system_admin`        → every client of the platform owner's firm (standing
                          access), plus every client of a firm it holds an
                          active access grant on (break-glass, time-limited).
- broker roles          → every client within their own broker firm.
- client roles          → only clients granted via `UserClientAccess`.

Role strings are duplicated here as literals (not imported from `app.core.auth`)
to avoid an import cycle: `auth` imports `identity`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenant_resolution import platform_owner_firm
from app.models import Client, UserClientAccess
from app.models.platform import GRANT_SCOPE_READ, GRANT_SCOPE_WRITE, PlatformAccessGrant

ROLE_SYSTEM_ADMIN = "system_admin"
BROKER_ROLES = frozenset({"broker_admin", "broker_viewer", "firm_admin"})
CLIENT_ROLES = frozenset({"client_admin", "client_hr"})

# How a system_admin reaches a firm: `standing` for the platform owner's firm,
# else the scope of its active access grant on that firm.
PlatformAccess = Literal["standing", "read", "write"]
PLATFORM_ACCESS_STANDING: PlatformAccess = "standing"


def _active_grant_scopes(
    db: Session, user_id: str, firm_id: str | None = None
) -> dict[str, PlatformAccess]:
    """Firm id → the widest scope of the user's live (unrevoked, unexpired) grants."""
    stmt = select(PlatformAccessGrant.broker_firm_id, PlatformAccessGrant.scope).where(
        PlatformAccessGrant.user_id == user_id,
        PlatformAccessGrant.revoked_at.is_(None),
        PlatformAccessGrant.expires_at > datetime.now(UTC),
    )
    if firm_id is not None:
        stmt = stmt.where(PlatformAccessGrant.broker_firm_id == firm_id)
    scopes: dict[str, PlatformAccess] = {}
    for grant_firm, scope in db.execute(stmt).all():
        if scope == GRANT_SCOPE_WRITE:
            scopes[grant_firm] = "write"
        elif scope == GRANT_SCOPE_READ:
            scopes.setdefault(grant_firm, "read")
    return scopes


def platform_reach(db: Session, user_id: str) -> dict[str, PlatformAccess]:
    """Every firm a system_admin may reach right now, with how it reaches it."""
    reach = _active_grant_scopes(db, user_id)
    owner = platform_owner_firm(db)
    if owner is not None:
        reach[owner.id] = PLATFORM_ACCESS_STANDING
    return reach


def platform_access_level(db: Session, user_id: str, firm_id: str) -> PlatformAccess | None:
    """How a system_admin reaches one firm, or None when it may not."""
    owner = platform_owner_firm(db)
    if owner is not None and owner.id == firm_id:
        return PLATFORM_ACCESS_STANDING
    return _active_grant_scopes(db, user_id, firm_id).get(firm_id)


def platform_access_for_client(db: Session, user_id: str, client_id: str) -> PlatformAccess | None:
    """`platform_access_level` for the firm that owns ``client_id``."""
    client = db.get(Client, client_id)
    if client is None:
        return None
    return platform_access_level(db, user_id, client.broker_firm_id)


def accessible_clients(
    *, role: str, broker_firm_id: str | None, user_id: str, db: Session
) -> list[Client]:
    """Clients the principal may act on, ordered for a stable default."""
    if role == ROLE_SYSTEM_ADMIN:
        reach = platform_reach(db, user_id)
        if not reach:
            return []
        stmt = (
            select(Client)
            .where(Client.broker_firm_id.in_(sorted(reach)))
            .order_by(Client.name, Client.id)
        )
        clients = list(db.execute(stmt).scalars().all())
        # Standing access first: a grant is break-glass, so it must never be
        # where a platform admin lands by default.
        return sorted(
            clients, key=lambda c: reach[c.broker_firm_id] != PLATFORM_ACCESS_STANDING
        )

    if role in BROKER_ROLES:
        if not broker_firm_id:
            return []
        stmt = (
            select(Client)
            .where(Client.broker_firm_id == broker_firm_id)
            .order_by(Client.name, Client.id)
        )
        return list(db.execute(stmt).scalars().all())

    # Client-scoped roles: only explicitly granted clients (and only within
    # their firm, in case a grant outlives a firm move).
    stmt = (
        select(Client)
        .join(UserClientAccess, UserClientAccess.client_id == Client.id)
        .where(UserClientAccess.user_id == user_id)
        .order_by(Client.name, Client.id)
    )
    clients = list(db.execute(stmt).scalars().all())
    if broker_firm_id:
        clients = [c for c in clients if c.broker_firm_id == broker_firm_id]
    return clients


def assert_client_accessible(
    *, role: str, broker_firm_id: str | None, user_id: str, client_id: str, db: Session
) -> Client | None:
    """Return the Client if the principal may act on it, else None."""
    client = db.get(Client, client_id)
    if client is None:
        return None
    if role == ROLE_SYSTEM_ADMIN:
        reachable = platform_access_level(db, user_id, client.broker_firm_id) is not None
        return client if reachable else None
    if role in BROKER_ROLES:
        return client if client.broker_firm_id == broker_firm_id else None
    # client-scoped: must have an explicit grant and be in the firm
    if broker_firm_id and client.broker_firm_id != broker_firm_id:
        return None
    grant = db.execute(
        select(UserClientAccess.id).where(
            UserClientAccess.user_id == user_id,
            UserClientAccess.client_id == client_id,
        )
    ).scalar_one_or_none()
    return client if grant is not None else None


def resolve_active_client_id(
    *,
    role: str,
    broker_firm_id: str | None,
    user_id: str,
    requested_client_id: str | None,
    db: Session,
) -> str | None:
    """Resolve the active client for a request.

    An explicit (header) selection is validated against the principal's access.
    With no selection, fall back to the first accessible client so single-client
    callers work without sending a header.
    """
    if requested_client_id:
        client = assert_client_accessible(
            role=role,
            broker_firm_id=broker_firm_id,
            user_id=user_id,
            client_id=requested_client_id,
            db=db,
        )
        if client is None:
            return None
        return client.id

    clients = accessible_clients(
        role=role, broker_firm_id=broker_firm_id, user_id=user_id, db=db
    )
    return clients[0].id if clients else None
