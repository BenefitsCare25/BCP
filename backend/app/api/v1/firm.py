"""Firm console: a broker firm's own settings, for its firm owners.

A `firm_admin` acts on its own firm; the platform master admin (`system_admin`)
on the selected company's firm, within its access model (`require_write_access`
makes a `read` grant read-only here). Domains added here start `pending`: the
platform console activates them once ownership and the certificate are
confirmed at the edge. A firm sees every master-admin access grant on it,
including revoked and expired ones. Firm owners also choose how the firm's
staff sign in (Microsoft 365 through the firm's own directory, email +
password with a mandatory authenticator, or both).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.platform import (
    DomainOut,
    GrantOut,
    SignInMethodsIn,
    SignInMethodsOut,
    Surface,
    add_domain,
    domain_out,
    firm_domains,
    grants_out,
    sign_in_methods_out,
    update_sign_in_methods,
)
from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser
from app.core.deps import require_client_id, require_firm_owner
from app.db.session import get_db
from app.models import BrokerFirm, Client
from app.models.platform import PlatformAccessGrant

router = APIRouter(prefix="/firm", tags=["firm"])


class FirmProfileOut(BaseModel):
    id: str
    name: str
    slug: str | None
    status: str
    is_platform_owner: bool
    allow_hide_attribution: bool


class FirmDomainCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=253)
    surface: Surface


def own_firm(db: Session, user: CurrentUser) -> BrokerFirm:
    """The caller's firm; for the platform admin, the selected company's."""
    if user.role == ROLE_SYSTEM_ADMIN:
        client = db.get(Client, require_client_id(user))
        firm_id = client.broker_firm_id if client is not None else None
    else:
        firm_id = user.broker_firm_id
    firm = db.get(BrokerFirm, firm_id) if firm_id else None
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    return firm


@router.get("/profile", response_model=FirmProfileOut)
def get_profile(
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> FirmProfileOut:
    firm = own_firm(db, user)
    return FirmProfileOut(
        id=firm.id,
        name=firm.name,
        slug=firm.slug,
        status=firm.status,
        is_platform_owner=firm.is_platform_owner,
        allow_hide_attribution=firm.allow_hide_attribution,
    )


@router.get("/domains", response_model=list[DomainOut])
def list_domains(
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> list[DomainOut]:
    return firm_domains(db, own_firm(db, user).id)


@router.post("/domains", response_model=DomainOut, status_code=201)
def request_domain(
    body: FirmDomainCreate,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> DomainOut:
    """Register a hostname for this firm. It stays `pending` (routes nothing)
    until the platform activates it."""
    firm = own_firm(db, user)
    domain = add_domain(
        db, user, firm, hostname=body.hostname, surface=body.surface, is_primary=False
    )
    return domain_out(domain)


@router.get("/platform-access", response_model=list[GrantOut])
def list_platform_access(
    limit: int = Query(200, ge=1, le=500),
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> list[GrantOut]:
    """Master-admin access grants on this firm, newest first."""
    firm = own_firm(db, user)
    return grants_out(db, PlatformAccessGrant.broker_firm_id == firm.id, limit=limit)


@router.get("/sign-in-methods", response_model=SignInMethodsOut)
def get_sign_in_methods(
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> SignInMethodsOut:
    """How this firm's staff sign in, and the directory consent link."""
    return sign_in_methods_out(db, own_firm(db, user).id)


@router.put("/sign-in-methods", response_model=SignInMethodsOut)
def put_sign_in_methods(
    body: SignInMethodsIn,
    user: CurrentUser = Depends(require_firm_owner),
    db: Session = Depends(get_db),
) -> SignInMethodsOut:
    """Set how this firm's staff sign in. At least one method stays on; taking
    one away ends the sessions opened with it."""
    return update_sign_in_methods(db, user, own_firm(db, user), body)
