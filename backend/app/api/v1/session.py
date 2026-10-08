"""Session / identity endpoint.

`GET /me` tells the frontend who is signed in, which client is active, and
which clients they may switch to. The active client is selected per request
via the `X-Inspro-Client` header (validated in `get_current_user`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser, get_current_user
from app.core.identity import PlatformAccess, accessible_clients
from app.core.tenant_resolution import request_firm
from app.db.session import get_db
from app.models import BrokerFirm, Client, User

router = APIRouter(tags=["session"])


class ClientSummary(BaseModel):
    id: str
    name: str
    # The company's broker firm: a platform admin's picker groups by firm.
    broker_firm_id: str
    firm_name: str


class FirmSummary(BaseModel):
    id: str
    name: str
    slug: str | None
    is_platform_owner: bool


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    role: str
    broker_firm_id: str | None
    active_client_id: str | None
    accessible_clients: list[ClientSummary]
    # The caller's broker firm; for the platform admin, the active company's.
    firm: FirmSummary | None = None
    # True for the platform admin on a platform host: the console is available.
    platform_console: bool = False
    # Platform admin only: how it reaches the active company's firm
    # (`standing`, or a `read` / `write` access grant); None otherwise.
    platform_access: PlatformAccess | None = None


def _caller_firm(db: Session, user: CurrentUser, active_client_id: str | None) -> BrokerFirm | None:
    if user.role != ROLE_SYSTEM_ADMIN:
        return db.get(BrokerFirm, user.broker_firm_id) if user.broker_firm_id else None
    client = db.get(Client, active_client_id) if active_client_id else None
    return db.get(BrokerFirm, client.broker_firm_id) if client is not None else None


@router.get("/me", response_model=MeResponse)
def get_me(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    clients = accessible_clients(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        db=db,
    )
    firm_names = dict(db.execute(
        select(BrokerFirm.id, BrokerFirm.name).where(
            BrokerFirm.id.in_({c.broker_firm_id for c in clients})
        )
    ).tuples().all()) if clients else {}
    record = db.get(User, user.user_id)
    active = user.client_id if any(c.id == user.client_id for c in clients) else None
    firm = _caller_firm(db, user, active)
    host = request_firm(request)
    return MeResponse(
        user_id=user.user_id,
        email=user.email or (record.email if record else None),
        display_name=record.display_name if record else None,
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        active_client_id=active,
        accessible_clients=[
            ClientSummary(
                id=c.id, name=c.name, broker_firm_id=c.broker_firm_id,
                firm_name=firm_names.get(c.broker_firm_id, ""),
            )
            for c in clients
        ],
        firm=FirmSummary(
            id=firm.id, name=firm.name, slug=firm.slug, is_platform_owner=firm.is_platform_owner,
        ) if firm is not None else None,
        platform_console=(
            user.role == ROLE_SYSTEM_ADMIN and host is not None and host.is_platform_host
        ),
        platform_access=user.platform_access if active else None,
    )
