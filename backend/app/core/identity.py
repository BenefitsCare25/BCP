"""Tenant access resolution: which clients a principal may act on, and
validation of the active client selected per request.

Access model (the hard boundary is the broker firm):
- `system_admin`        → every client, across all firms (cross-firm, audited).
- broker roles          → every client within their own broker firm.
- client roles          → only clients granted via `UserClientAccess`.

Role strings are duplicated here as literals (not imported from `app.core.auth`)
to avoid an import cycle: `auth` imports `identity`.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Client, UserClientAccess

ROLE_SYSTEM_ADMIN = "system_admin"
BROKER_ROLES = frozenset({"broker_admin", "broker_viewer"})
CLIENT_ROLES = frozenset({"client_admin", "client_hr"})


def accessible_clients(
    *, role: str, broker_firm_id: str | None, user_id: str, db: Session
) -> list[Client]:
    """Clients the principal may act on, ordered for a stable default."""
    if role == ROLE_SYSTEM_ADMIN:
        stmt = select(Client).order_by(Client.name, Client.id)
        return list(db.execute(stmt).scalars().all())

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
        return client
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
