"""Session / identity endpoint.

`GET /me` tells the frontend who is signed in, which client is active, and
which clients they may switch to. The active client is selected per request
via the `X-Inspro-Client` header (validated in `get_current_user`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.identity import accessible_clients
from app.db.session import get_db
from app.models import User

router = APIRouter(tags=["session"])


class ClientSummary(BaseModel):
    id: str
    name: str


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    role: str
    broker_firm_id: str | None
    active_client_id: str | None
    accessible_clients: list[ClientSummary]


@router.get("/me", response_model=MeResponse)
def get_me(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MeResponse:
    clients = accessible_clients(
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        user_id=user.user_id,
        db=db,
    )
    record = db.get(User, user.user_id)
    return MeResponse(
        user_id=user.user_id,
        email=user.email or (record.email if record else None),
        display_name=record.display_name if record else None,
        role=user.role,
        broker_firm_id=user.broker_firm_id,
        active_client_id=user.client_id,
        accessible_clients=[ClientSummary(id=c.id, name=c.name) for c in clients],
    )
