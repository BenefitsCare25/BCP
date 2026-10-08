"""Platform console: broker firms, their domains, master-admin access grants and
the platform audit trail.

Master admin (`system_admin`) only, and only on a platform host
(`Settings.platform_hosts`): on a broker's own domain every route is a 404, so
a white-label host never exposes the console. Every change is written to
`platform_audit_log`; firm and domain changes drop the host-resolution cache so
they route at once.

Access grants are the break-glass path into any firm other than the platform
owner's (where the master admin has standing access): reason-stated, `read` or
`write`, at most `MAX_GRANT_HOURS` long. Creation and revocation are also
written into the firm's own `audit_log`, so the firm sees who entered and why.

The domain, grant and sign-in method shapes and helpers here are shared with
the firm console (`api/v1/firm.py`).

Each firm chooses how its staff sign in (`core/identity_providers.py`); a new
firm starts with email + password, so its first firm admin can accept the
invitation without a Microsoft directory.
"""
from __future__ import annotations

import re
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, not_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core import identity_providers as IDP
from app.core.audit import write_audit, write_platform_audit
from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser
from app.core.broker_auth import SUBJECT as SUBJECT_BROKER
from app.core.deps import require_system_admin
from app.core.request_context import get_client_ip
from app.core.settings import get_settings
from app.core.tenancy_host import SlugError, validate_slug
from app.core.tenant_resolution import (
    invalidate_firm_cache,
    platform_owner_firm,
    require_platform_host,
)
from app.db.session import get_db
from app.db.tenancy import provision_new_firm, set_search_path
from app.models import AuthSession, BrokerFirm, Client, User
from app.models.auth import SUBJECT_USER
from app.models.client import FIRM_STATUS_SUSPENDED
from app.models.platform import (
    DOMAIN_STATUS_ACTIVE,
    DOMAIN_STATUS_PENDING,
    IDP_LOCAL,
    MAX_GRANT_HOURS,
    IdentityProvider,
    PlatformAccessGrant,
    PlatformAuditLog,
    TenantDomain,
)
from app.services.client_slug import slugify_client_name

router = APIRouter(
    prefix="/platform",
    tags=["platform"],
    # The host check runs first: off a platform host the console does not
    # exist (404), whoever asks.
    dependencies=[Depends(require_platform_host), Depends(require_system_admin)],
)

Surface = Literal["all", "staff", "client"]
DomainStatus = Literal["pending", "active", "disabled"]
FirmStatus = Literal["active", "suspended"]
GrantScope = Literal["read", "write"]

# Firm slugs that would collide with platform hostnames (`platform.<fallback>`).
_FIRM_RESERVED_SLUGS = frozenset({"platform"})
_MAX_LABEL = 63
_DNS_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_SLUG_TAKEN = "Another broker firm already uses this slug."
_HOSTNAME_TAKEN = "This hostname is already registered."
_MIN_REASON = 15


# ── Shapes ────────────────────────────────────────────────────────────────────
class FirmOut(BaseModel):
    id: str
    name: str
    slug: str | None
    status: str
    is_platform_owner: bool
    allow_hide_attribution: bool
    client_count: int
    domain_count: int
    created_at: datetime


class FirmCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Derived from the name when omitted.
    slug: str | None = Field(default=None, max_length=_MAX_LABEL)


class FirmPatch(BaseModel):
    """Partial update: only the fields sent are applied."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=_MAX_LABEL)
    status: FirmStatus | None = None
    allow_hide_attribution: bool | None = None


class DomainOut(BaseModel):
    id: str
    hostname: str
    surface: str
    is_primary: bool
    status: str
    verified_at: datetime | None
    created_at: datetime


class DomainCreate(BaseModel):
    hostname: str = Field(min_length=1, max_length=253)
    surface: Surface
    is_primary: bool = False


class DomainPatch(BaseModel):
    status: DomainStatus | None = None
    surface: Surface | None = None
    is_primary: bool | None = None


class GrantOut(BaseModel):
    id: str
    user_id: str
    user_email: str | None
    broker_firm_id: str
    firm_name: str | None
    reason: str
    scope: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


class GrantCreate(BaseModel):
    broker_firm_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(max_length=1000)
    scope: GrantScope
    hours: int = Field(ge=1, le=MAX_GRANT_HOURS)

    @field_validator("reason")
    @classmethod
    def _stated_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < _MIN_REASON:
            raise ValueError(f"State a reason of at least {_MIN_REASON} characters.")
        return value


class PlatformAuditOut(BaseModel):
    id: str
    occurred_at: datetime
    actor_user_id: str | None
    actor_email: str | None
    action: str
    entity_type: str
    entity_id: str | None
    broker_firm_id: str | None
    client_id: str | None
    detail: dict[str, Any] | None


class EntraSignInSetting(BaseModel):
    enabled: bool
    # The firm's Microsoft directory (tenant) id; required while enabled.
    tenant_id: str | None = Field(default=None, max_length=64)
    require_platform_mfa: bool = False


class LocalSignInSetting(BaseModel):
    enabled: bool


class SignInMethodsIn(BaseModel):
    entra: EntraSignInSetting
    local: LocalSignInSetting


class SignInMethodsOut(SignInMethodsIn):
    # Where the firm's IT administrator consents to the platform app in its
    # directory; None until a directory id is saved.
    admin_consent_url: str | None


# ── Shared helpers (also used by api/v1/firm.py) ──────────────────────────────
def _utc(value: datetime) -> datetime:
    """Timestamps leave as UTC: SQLite (development) hands them back naive."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _commit(db: Session, conflict: str) -> None:
    """Commit, turning a unique-constraint race into the same 409 as the check."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, conflict) from None


def validate_hostname(raw: str) -> str:
    """A customer hostname, normalized, or 422.

    Lowercase, at most 253 characters, at least two valid DNS labels, not an IP
    address, not a platform host, not `*.localhost` in production, and not under
    the platform's own domains, whose hosts the platform routes itself: the
    firms' neutral fallback domain (`Settings.firm_fallback_domain`,
    `<firm-slug>.<fallback>`) and the legacy base domain (`Settings.base_domain`).
    """
    settings = get_settings()
    host = (raw or "").strip().lower().rstrip(".")
    labels = host.split(".")
    if not host or len(host) > 253 or len(labels) < 2 or not all(
        _DNS_LABEL.match(label) for label in labels
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Enter a hostname such as benefits.example.com.",
        )
    if labels[-1].isdigit():  # no top-level domain is numeric: an IPv4 address
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a hostname, not an IP address."
        )
    if host in settings.platform_hosts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "This hostname is a platform host."
        )
    if settings.env == "prod" and host.endswith(".localhost"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "A .localhost hostname cannot serve a broker."
        )
    for reserved in (settings.firm_fallback_domain, settings.base_domain):
        base = reserved.strip().lower().strip(".")
        if base and (host == base or host.endswith(f".{base}")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Hostnames under {base} are reserved for the platform.",
            )
    return host


def domain_out(domain: TenantDomain) -> DomainOut:
    return DomainOut(
        id=domain.id,
        hostname=domain.hostname,
        surface=domain.surface,
        is_primary=domain.is_primary,
        status=domain.status,
        verified_at=_utc(domain.verified_at) if domain.verified_at else None,
        created_at=_utc(domain.created_at),
    )


def firm_domains(db: Session, firm_id: str) -> list[DomainOut]:
    rows = db.execute(
        select(TenantDomain)
        .where(TenantDomain.broker_firm_id == firm_id)
        .order_by(TenantDomain.hostname)
    ).scalars().all()
    return [domain_out(row) for row in rows]


def _keep_single_primary(db: Session, domain: TenantDomain) -> None:
    """One primary hostname per firm per surface: this one clears the others."""
    if not domain.is_primary:
        return
    db.execute(
        update(TenantDomain)
        .where(
            TenantDomain.broker_firm_id == domain.broker_firm_id,
            TenantDomain.surface == domain.surface,
            TenantDomain.id != domain.id,
            TenantDomain.is_primary.is_(True),
        )
        .values(is_primary=False)
    )


def add_domain(
    db: Session,
    user: CurrentUser,
    firm: BrokerFirm,
    *,
    hostname: str,
    surface: str,
    is_primary: bool,
) -> TenantDomain:
    """Register a `pending` hostname for ``firm``, audited. Commits.

    Nothing routes to it until the platform activates it.
    """
    host = validate_hostname(hostname)
    if db.execute(
        select(TenantDomain.id).where(TenantDomain.hostname == host)
    ).scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, _HOSTNAME_TAKEN)
    domain = TenantDomain(
        broker_firm_id=firm.id,
        hostname=host,
        surface=surface,
        is_primary=is_primary,
        status=DOMAIN_STATUS_PENDING,
        created_by=user.user_id,
    )
    db.add(domain)
    db.flush()
    _keep_single_primary(db, domain)
    write_platform_audit(
        db, user, action="domain.create", entity_type="tenant_domain", entity_id=domain.id,
        broker_firm_id=firm.id,
        detail={"hostname": host, "surface": surface, "is_primary": is_primary},
    )
    _commit(db, _HOSTNAME_TAKEN)
    invalidate_firm_cache()
    return domain


def grants_out(
    db: Session, *conditions: ColumnElement[bool], limit: int
) -> list[GrantOut]:
    """Access grants, newest first, with the admin's email and the firm's name."""
    rows = db.execute(
        select(PlatformAccessGrant, User.email, BrokerFirm.name)
        .outerjoin(User, User.id == PlatformAccessGrant.user_id)
        .outerjoin(BrokerFirm, BrokerFirm.id == PlatformAccessGrant.broker_firm_id)
        .where(*conditions)
        .order_by(PlatformAccessGrant.created_at.desc(), PlatformAccessGrant.id)
        .limit(limit)
    ).all()
    return [_grant_out(grant, email, firm_name) for grant, email, firm_name in rows]


def _grant_out(grant: PlatformAccessGrant, email: str | None, firm_name: str | None) -> GrantOut:
    return GrantOut(
        id=grant.id,
        user_id=grant.user_id,
        user_email=email,
        broker_firm_id=grant.broker_firm_id,
        firm_name=firm_name,
        reason=grant.reason,
        scope=grant.scope,
        expires_at=_utc(grant.expires_at),
        revoked_at=_utc(grant.revoked_at) if grant.revoked_at else None,
        created_at=_utc(grant.created_at),
    )


def sign_in_methods_out(db: Session, firm_id: str) -> SignInMethodsOut:
    current = IDP.sign_in_settings(db, firm_id)
    return SignInMethodsOut(
        entra=EntraSignInSetting(
            enabled=current.entra_enabled,
            tenant_id=current.tenant_id,
            require_platform_mfa=current.require_platform_mfa,
        ),
        local=LocalSignInSetting(enabled=current.local_enabled),
        admin_consent_url=IDP.admin_consent_url(IDP.saved_directory(db, firm_id)),
    )


def _revoke_method_sessions(
    db: Session, firm_id: str, *, microsoft: bool, unverified_only: bool = False
) -> int:
    """End the live staff sessions of one sign-in method in a firm. No commit.

    An account bound to Microsoft signs in with Microsoft; any other staff
    account with a password. The master admin signs in through the platform
    owner's firm, so its sessions count there.
    """
    scope = User.broker_firm_id == firm_id
    owner = platform_owner_firm(db)
    if microsoft and owner is not None and owner.id == firm_id:
        scope = or_(scope, and_(User.broker_firm_id.is_(None), User.role == ROLE_SYSTEM_ADMIN))
    bound = User.external_id.is_not(None) if microsoft else User.external_id.is_(None)
    stmt = update(AuthSession).where(
        AuthSession.subject_type == SUBJECT_BROKER,
        AuthSession.revoked_at.is_(None),
        AuthSession.subject_id.in_(select(User.id).where(scope, bound)),
    )
    if unverified_only:
        stmt = stmt.where(AuthSession.mfa_verified.is_(False))
    result = db.execute(
        stmt.values(revoked_at=datetime.now(UTC)).execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _end_replaced_sessions(
    db: Session, firm_id: str, before: IDP.SignInSettings, after: IDP.SignInSettings
) -> int:
    """End the sessions a sign-in method change takes away. No commit.

    Switching a method off (or moving Microsoft sign-in to another directory)
    ends that method's sessions; newly requiring the authenticator after
    Microsoft ends the Microsoft sessions that have not passed it.
    """
    revoked = 0
    if before.local_enabled and not after.local_enabled:
        revoked += _revoke_method_sessions(db, firm_id, microsoft=False)
    if before.entra_enabled and (
        not after.entra_enabled or after.tenant_id != before.tenant_id
    ):
        revoked += _revoke_method_sessions(db, firm_id, microsoft=True)
    elif after.entra_enabled and after.require_platform_mfa and not (
        before.entra_enabled and before.require_platform_mfa
    ):
        revoked += _revoke_method_sessions(db, firm_id, microsoft=True, unverified_only=True)
    return revoked


def update_sign_in_methods(
    db: Session, user: CurrentUser, firm: BrokerFirm, body: SignInMethodsIn
) -> SignInMethodsOut:
    """Validate and save a firm's sign-in methods, end the sessions the change
    takes away, and audit it in the firm's trail (and the platform's, for the
    master admin). Commits."""
    _load_firm(db, firm.id, lock=True)  # one change at a time per firm
    before = IDP.sign_in_settings(db, firm.id)
    after = IDP.validate_settings(db, firm.id, IDP.SignInSettings(
        entra_enabled=body.entra.enabled,
        tenant_id=body.entra.tenant_id,
        require_platform_mfa=body.entra.require_platform_mfa,
        local_enabled=body.local.enabled,
    ))
    if after == before:
        return sign_in_methods_out(db, firm.id)
    IDP.save_settings(db, firm.id, after)
    revoked = _end_replaced_sessions(db, firm.id, before, after)
    if user.role == ROLE_SYSTEM_ADMIN:
        write_platform_audit(
            db, user, action="firm.sign_in_methods", entity_type="broker_firm",
            entity_id=firm.id, broker_firm_id=firm.id,
            detail={"before": asdict(before), "after": asdict(after),
                    "sessions_revoked": revoked},
        )
    set_search_path(db, firm.id)
    write_audit(
        db, user, action="update", entity_type="sign_in_methods", entity_id=firm.id,
        before=asdict(before), after={**asdict(after), "sessions_revoked": revoked},
        client_id=None,
    )
    _commit(db, "The sign-in methods were changed at the same time. Try again.")
    return sign_in_methods_out(db, firm.id)


# ── Firms ─────────────────────────────────────────────────────────────────────
def validate_firm_slug(raw: str) -> str:
    """A firm slug: a DNS label, not reserved for tenants or the platform."""
    slug = validate_slug(raw)
    if slug in _FIRM_RESERVED_SLUGS:
        raise SlugError(f"'{slug}' is reserved and cannot be used as a slug.")
    return slug


def derive_firm_slug(name: str) -> str:
    """A valid firm slug candidate from a firm name (uniqueness not checked)."""
    base = slugify_client_name(name)
    return f"{base}-firm" if base in _FIRM_RESERVED_SLUGS else base


def _slug_taken(db: Session, slug: str, exclude_id: str | None) -> bool:
    stmt = select(BrokerFirm.id).where(BrokerFirm.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(BrokerFirm.id != exclude_id)
    return db.execute(stmt.limit(1)).scalar_one_or_none() is not None


def _firm_slug(
    db: Session, name: str, requested: str | None, *, exclude_id: str | None = None
) -> str:
    """The requested slug, validated and unique; or one derived from ``name``."""
    if requested is not None:
        try:
            slug = validate_firm_slug(requested)
        except SlugError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if _slug_taken(db, slug, exclude_id):
            raise HTTPException(status.HTTP_409_CONFLICT, _SLUG_TAKEN)
        return slug
    base = derive_firm_slug(name)
    if not _slug_taken(db, base, exclude_id):
        return base
    for n in range(2, 100):
        candidate = f"{base[: _MAX_LABEL - len(str(n)) - 1]}-{n}"
        if not _slug_taken(db, candidate, exclude_id):
            return candidate
    return f"{base[:48]}-{secrets.token_hex(3)}"


def _clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Enter the firm's name.")
    return name


def _firm_out(db: Session, firm: BrokerFirm) -> FirmOut:
    clients = db.scalar(select(func.count(Client.id)).where(Client.broker_firm_id == firm.id))
    domains = db.scalar(
        select(func.count(TenantDomain.id)).where(TenantDomain.broker_firm_id == firm.id)
    )
    return _firm_row(firm, int(clients or 0), int(domains or 0))


def _firm_row(firm: BrokerFirm, clients: int, domains: int) -> FirmOut:
    return FirmOut(
        id=firm.id,
        name=firm.name,
        slug=firm.slug,
        status=firm.status,
        is_platform_owner=firm.is_platform_owner,
        allow_hide_attribution=firm.allow_hide_attribution,
        client_count=clients,
        domain_count=domains,
        created_at=_utc(firm.created_at),
    )


def _firm_state(firm: BrokerFirm) -> dict[str, Any]:
    return {
        "name": firm.name,
        "slug": firm.slug,
        "status": firm.status,
        "allow_hide_attribution": firm.allow_hide_attribution,
    }


def _revoke_firm_sessions(db: Session, firm_id: str) -> int:
    """End every live session of a firm: its staff (broker and HR) and its
    companies' members. No commit."""
    firm_users = select(User.id).where(User.broker_firm_id == firm_id)
    firm_clients = select(Client.id).where(Client.broker_firm_id == firm_id)
    result = db.execute(
        update(AuthSession)
        .where(
            AuthSession.revoked_at.is_(None),
            or_(
                AuthSession.broker_firm_id == firm_id,
                AuthSession.client_id.in_(firm_clients),
                and_(
                    AuthSession.subject_type.in_((SUBJECT_BROKER, SUBJECT_USER)),
                    AuthSession.subject_id.in_(firm_users),
                ),
            ),
        )
        .values(revoked_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def _load_firm(db: Session, firm_id: str, *, lock: bool = False) -> BrokerFirm:
    stmt = select(BrokerFirm).where(BrokerFirm.id == firm_id)
    firm = db.execute(stmt.with_for_update() if lock else stmt).scalar_one_or_none()
    if firm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Broker firm not found")
    return firm


@router.get("/firms", response_model=list[FirmOut])
def list_firms(
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[FirmOut]:
    clients: dict[str, int] = {
        firm_id: int(count)
        for firm_id, count in db.execute(
            select(Client.broker_firm_id, func.count(Client.id)).group_by(Client.broker_firm_id)
        ).all()
    }
    domains: dict[str, int] = {
        firm_id: int(count)
        for firm_id, count in db.execute(
            select(TenantDomain.broker_firm_id, func.count(TenantDomain.id))
            .group_by(TenantDomain.broker_firm_id)
        ).all()
    }
    firms = db.execute(
        select(BrokerFirm).order_by(BrokerFirm.is_platform_owner.desc(), BrokerFirm.name)
    ).scalars().all()
    return [_firm_row(f, clients.get(f.id, 0), domains.get(f.id, 0)) for f in firms]


@router.post("/firms", response_model=FirmOut, status_code=201)
def create_firm(
    body: FirmCreate,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> FirmOut:
    """Create a broker firm and its schema. Its first `firm_admin` is invited
    through `POST /admin/invitations` naming this firm."""
    name = _clean_name(body.name)
    firm = BrokerFirm(name=name, slug=_firm_slug(db, name, body.slug))
    db.add(firm)
    db.flush()  # assigns firm.id; not yet committed
    # Provision on THIS request's connection so the row and the schema commit
    # or roll back together: an orphaned firm (row, no schema) would fail every
    # future login for it. A second connection would also deadlock — tenant
    # tables that reference broker_firms need a SHARE ROW EXCLUSIVE lock, which
    # waits forever on this transaction's uncommitted INSERT. No-op on SQLite.
    # Left pending (503 until the migration job provisions it) when the app
    # runs without DDL rights: INSPRO_RUNTIME_PROVISIONING=false.
    provisioned = provision_new_firm(db.connection(), firm.id)
    # Password sign-in to start: the first firm admin accepts the invitation
    # with a password; Microsoft sign-in needs the firm's directory first.
    db.add(IdentityProvider(broker_firm_id=firm.id, kind=IDP_LOCAL, enabled=True))
    write_platform_audit(
        db, user, action="firm.create", entity_type="broker_firm", entity_id=firm.id,
        broker_firm_id=firm.id,
        detail={
            "name": firm.name, "slug": firm.slug, "sign_in_methods": [IDP_LOCAL],
            "provisioned": provisioned,
        },
    )
    _commit(db, _SLUG_TAKEN)
    invalidate_firm_cache()
    return _firm_out(db, firm)


@router.patch("/firms/{firm_id}", response_model=FirmOut)
def patch_firm(
    firm_id: str,
    body: FirmPatch,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> FirmOut:
    """Rename, re-slug, suspend/reactivate or change entitlements.

    The platform owner's firm cannot be suspended. Suspending a firm ends every
    live session of its staff and of its companies' members at once.
    """
    firm = _load_firm(db, firm_id, lock=True)
    sent = body.model_fields_set
    before = _firm_state(firm)
    if "name" in sent and body.name is not None:
        firm.name = _clean_name(body.name)
    if "slug" in sent and body.slug is not None:
        firm.slug = _firm_slug(db, firm.name, body.slug, exclude_id=firm.id)
    if "allow_hide_attribution" in sent and body.allow_hide_attribution is not None:
        firm.allow_hide_attribution = body.allow_hide_attribution
    revoked = 0
    if "status" in sent and body.status is not None and body.status != firm.status:
        owner = platform_owner_firm(db)
        if body.status == FIRM_STATUS_SUSPENDED and owner is not None and owner.id == firm.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "The platform owner's firm cannot be suspended."
            )
        firm.status = body.status
        if body.status == FIRM_STATUS_SUSPENDED:
            revoked = _revoke_firm_sessions(db, firm.id)
    after = _firm_state(firm)
    if after != before:
        write_platform_audit(
            db, user, action="firm.update", entity_type="broker_firm", entity_id=firm.id,
            broker_firm_id=firm.id,
            detail={"before": before, "after": after, "sessions_revoked": revoked},
        )
    _commit(db, _SLUG_TAKEN)
    invalidate_firm_cache()
    return _firm_out(db, firm)


@router.get("/firms/{firm_id}/sign-in-methods", response_model=SignInMethodsOut)
def get_firm_sign_in_methods(
    firm_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> SignInMethodsOut:
    return sign_in_methods_out(db, _load_firm(db, firm_id).id)


@router.put("/firms/{firm_id}/sign-in-methods", response_model=SignInMethodsOut)
def put_firm_sign_in_methods(
    firm_id: str,
    body: SignInMethodsIn,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> SignInMethodsOut:
    """Set how the firm's staff sign in. Taking a method away ends its sessions."""
    return update_sign_in_methods(db, user, _load_firm(db, firm_id), body)


# ── Domains ───────────────────────────────────────────────────────────────────
def _load_domain(db: Session, domain_id: str) -> TenantDomain:
    domain = db.execute(
        select(TenantDomain).where(TenantDomain.id == domain_id).with_for_update()
    ).scalar_one_or_none()
    if domain is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Domain not found")
    return domain


def _domain_state(domain: TenantDomain) -> dict[str, Any]:
    return {"status": domain.status, "surface": domain.surface, "is_primary": domain.is_primary}


@router.get("/firms/{firm_id}/domains", response_model=list[DomainOut])
def list_firm_domains(
    firm_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[DomainOut]:
    return firm_domains(db, _load_firm(db, firm_id).id)


@router.post("/firms/{firm_id}/domains", response_model=DomainOut, status_code=201)
def create_firm_domain(
    firm_id: str,
    body: DomainCreate,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DomainOut:
    firm = _load_firm(db, firm_id)
    domain = add_domain(
        db, user, firm, hostname=body.hostname, surface=body.surface, is_primary=body.is_primary
    )
    return domain_out(domain)


@router.patch("/domains/{domain_id}", response_model=DomainOut)
def patch_domain(
    domain_id: str,
    body: DomainPatch,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DomainOut:
    """Activate (routes traffic; stamps `verified_at`), disable, or change the
    surface or primary flag. One primary per firm per surface."""
    domain = _load_domain(db, domain_id)
    before = _domain_state(domain)
    if body.status is not None:
        if body.status == DOMAIN_STATUS_ACTIVE and domain.status != DOMAIN_STATUS_ACTIVE:
            domain.verified_at = datetime.now(UTC)
        domain.status = body.status
    if body.surface is not None:
        domain.surface = body.surface
    if body.is_primary is not None:
        domain.is_primary = body.is_primary
    _keep_single_primary(db, domain)
    after = _domain_state(domain)
    if after != before:
        write_platform_audit(
            db, user, action="domain.update", entity_type="tenant_domain", entity_id=domain.id,
            broker_firm_id=domain.broker_firm_id,
            detail={"hostname": domain.hostname, "before": before, "after": after},
        )
    _commit(db, _HOSTNAME_TAKEN)
    invalidate_firm_cache()
    return domain_out(domain)


@router.delete("/domains/{domain_id}", status_code=204)
def delete_domain(
    domain_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> Response:
    domain = _load_domain(db, domain_id)
    write_platform_audit(
        db, user, action="domain.delete", entity_type="tenant_domain", entity_id=domain.id,
        broker_firm_id=domain.broker_firm_id,
        detail={"hostname": domain.hostname, **_domain_state(domain)},
    )
    db.delete(domain)
    db.commit()
    invalidate_firm_cache()
    return Response(status_code=204)


# ── Access grants ─────────────────────────────────────────────────────────────
def _live_grant() -> ColumnElement[bool]:
    return and_(
        PlatformAccessGrant.revoked_at.is_(None),
        PlatformAccessGrant.expires_at > datetime.now(UTC),
    )


def _audit_in_firm(
    db: Session, user: CurrentUser, grant: PlatformAccessGrant, action: str,
    detail: dict[str, Any],
) -> None:
    """Record a grant change in the firm's own audit trail as well as the
    platform's, so the firm sees who entered its data and why."""
    write_platform_audit(
        db, user, action=f"access_grant.{action}", entity_type="platform_access",
        entity_id=grant.id, broker_firm_id=grant.broker_firm_id, detail=detail,
    )
    set_search_path(db, grant.broker_firm_id)
    write_audit(
        db, user, action=f"platform_access.{action}", entity_type="platform_access",
        entity_id=grant.id, after=detail, client_id=None,
    )


def _grant_detail(grant: PlatformAccessGrant) -> dict[str, Any]:
    return {
        "user_id": grant.user_id,
        "scope": grant.scope,
        "reason": grant.reason,
        "expires_at": _utc(grant.expires_at).isoformat(),
    }


def _one_grant(db: Session, grant_id: str) -> GrantOut:
    return grants_out(db, PlatformAccessGrant.id == grant_id, limit=1)[0]


@router.get("/access-grants", response_model=list[GrantOut])
def list_access_grants(
    active: bool | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[GrantOut]:
    """Grants across firms, newest first: `active=true` live ones only,
    `active=false` revoked or expired ones only, omitted for both."""
    conditions: list[ColumnElement[bool]] = []
    if active is not None:
        conditions.append(_live_grant() if active else not_(_live_grant()))
    return grants_out(db, *conditions, limit=limit)


@router.post("/access-grants", response_model=GrantOut, status_code=201)
def create_access_grant(
    body: GrantCreate,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> GrantOut:
    firm = _load_firm(db, body.broker_firm_id)
    owner = platform_owner_firm(db)
    if owner is not None and owner.id == firm.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You already have standing access to the platform owner's firm.",
        )
    now = datetime.now(UTC)
    ip = get_client_ip()
    grant = PlatformAccessGrant(
        user_id=user.user_id,
        broker_firm_id=firm.id,
        reason=body.reason,
        scope=body.scope,
        expires_at=now + timedelta(hours=body.hours),
        created_ip=ip[:64] if ip else None,
        created_at=now,
    )
    db.add(grant)
    db.flush()
    _audit_in_firm(db, user, grant, "create", {**_grant_detail(grant), "hours": body.hours})
    db.commit()
    return _one_grant(db, grant.id)


@router.post("/access-grants/{grant_id}/revoke", response_model=GrantOut)
def revoke_access_grant(
    grant_id: str,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> GrantOut:
    """End a grant now. Revoking one already revoked changes nothing."""
    grant = db.execute(
        select(PlatformAccessGrant).where(PlatformAccessGrant.id == grant_id).with_for_update()
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Access grant not found")
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(UTC)
        grant.revoked_by = user.user_id
        _audit_in_firm(db, user, grant, "revoke", _grant_detail(grant))
        db.commit()
    return _one_grant(db, grant.id)


# ── Platform audit trail ──────────────────────────────────────────────────────
@router.get("/audit", response_model=list[PlatformAuditOut])
def list_platform_audit(
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = Query(None),
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[PlatformAuditOut]:
    """Newest first; pass the last row's `occurred_at` as `before` for the next page."""
    stmt = select(PlatformAuditLog, User.email).outerjoin(
        User, User.id == PlatformAuditLog.actor_user_id
    )
    if before is not None:
        cutoff = before.astimezone(UTC) if before.tzinfo else before.replace(tzinfo=UTC)
        stmt = stmt.where(PlatformAuditLog.occurred_at < cutoff)
    rows = db.execute(
        stmt.order_by(PlatformAuditLog.occurred_at.desc(), PlatformAuditLog.id.desc())
        .limit(limit)
    ).all()
    return [
        PlatformAuditOut(
            id=row.id,
            occurred_at=_utc(row.occurred_at),
            actor_user_id=row.actor_user_id,
            actor_email=email,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            broker_firm_id=row.broker_firm_id,
            client_id=row.client_id,
            detail=row.detail,
        )
        for row, email in rows
    ]
