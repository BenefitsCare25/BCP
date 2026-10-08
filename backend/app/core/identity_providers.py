"""How a broker firm's staff sign in: Microsoft 365, email + password, or both.

Each firm chooses its methods (`identity_providers`, one row per kind):

- `entra`: the firm's OWN Microsoft Entra directory, named by its tenant id.
  Tokens are validated against that directory only (`core/entra.py`).
- `local`: email + password with a mandatory authenticator
  (`api/v1/broker_auth.py` login endpoints).

Rows win. A firm with no rows keeps the behaviour from before the table existed:
the platform owner's firm in Entra mode signs in with the platform directory
(`INSPRO_ENTRA_TENANT_ID`); every other firm has password sign-in only. A
disabled row is off.

HR users and employees are unaffected: they have their own credential surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entra import AUTHORITY, is_directory_id
from app.core.settings import Settings, get_settings
from app.core.tenant_resolution import FirmContext, platform_owner_firm
from app.models.platform import IDP_ENTRA, IDP_LOCAL, IdentityProvider


@dataclass(frozen=True)
class EntraMethod:
    """Microsoft sign-in through one directory."""

    tenant_id: str
    # Also require the platform authenticator after Microsoft.
    require_platform_mfa: bool = False


@dataclass(frozen=True)
class SignInMethods:
    """The methods a firm's staff may use right now."""

    entra: EntraMethod | None
    local: bool


@dataclass(frozen=True)
class SignInSettings:
    """A firm's sign-in settings as its owners edit them.

    Unlike `SignInMethods`, a disabled Microsoft method keeps its directory id,
    so turning it back on does not need it typed again.
    """

    entra_enabled: bool
    tenant_id: str | None
    require_platform_mfa: bool
    local_enabled: bool


def normalize_tenant_id(raw: str | None) -> str | None:
    """A directory id in canonical form (lowercase GUID), None when blank, or 422."""
    value = (raw or "").strip().lower()
    if not value:
        return None
    if not is_directory_id(value):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Enter the Microsoft directory (tenant) ID, a GUID such as "
            "00000000-0000-0000-0000-000000000000.",
        )
    return value


def platform_directory(settings: Settings | None = None) -> EntraMethod | None:
    """The platform directory, in Entra mode only."""
    settings = settings or get_settings()
    tenant = settings.entra_tenant_id.strip().lower()
    if settings.auth_mode != "entra" or not tenant:
        return None
    return EntraMethod(tenant_id=tenant)


def _rows(db: Session, firm_id: str) -> dict[str, IdentityProvider]:
    rows = db.execute(
        select(IdentityProvider).where(IdentityProvider.broker_firm_id == firm_id)
    ).scalars().all()
    return {row.kind: row for row in rows}


def _defaults(db: Session, firm_id: str) -> SignInSettings:
    """What a firm with no rows offers (see the module docstring)."""
    implicit = platform_directory()
    owner = platform_owner_firm(db) if implicit is not None else None
    if implicit is not None and owner is not None and owner.id == firm_id:
        return SignInSettings(True, implicit.tenant_id, False, False)
    return SignInSettings(False, None, False, True)


def sign_in_settings(db: Session, firm_id: str) -> SignInSettings:
    """The firm's effective sign-in settings (rows, else the defaults)."""
    rows = _rows(db, firm_id)
    if not rows:
        return _defaults(db, firm_id)
    entra, local = rows.get(IDP_ENTRA), rows.get(IDP_LOCAL)
    return SignInSettings(
        entra_enabled=bool(entra and entra.enabled and entra.entra_tenant_id),
        tenant_id=entra.entra_tenant_id if entra else None,
        require_platform_mfa=bool(entra and entra.require_platform_mfa),
        local_enabled=bool(local and local.enabled),
    )


def saved_directory(db: Session, firm_id: str) -> str | None:
    """The directory id the firm's owners saved, if any (the platform owner's
    implicit platform directory is not one)."""
    entra = _rows(db, firm_id).get(IDP_ENTRA)
    return entra.entra_tenant_id if entra is not None else None


def firm_sign_in_methods(db: Session, firm_id: str) -> SignInMethods:
    """The sign-in methods a firm's staff may use."""
    current = sign_in_settings(db, firm_id)
    entra = (
        EntraMethod(current.tenant_id, current.require_platform_mfa)
        if current.entra_enabled and current.tenant_id
        else None
    )
    return SignInMethods(entra=entra, local=current.local_enabled)


def host_directory(db: Session, firm: FirmContext | None) -> EntraMethod | None:
    """The directory Microsoft tokens are validated against on this request's host.

    The host's firm decides. Without a firm context (local tools, tests with
    several firms) the platform directory applies.
    """
    if firm is None:
        return platform_directory()
    return firm_sign_in_methods(db, firm.firm_id).entra


def staff_firm_id(db: Session, firm_id: str | None, role: str) -> str | None:
    """The firm whose methods govern a staff account's sign-in.

    The master admin belongs to no firm and signs in on platform hosts, which
    serve the platform owner's firm.
    """
    if firm_id is not None or role != "system_admin":
        return firm_id
    owner = platform_owner_firm(db)
    return owner.id if owner is not None else None


def staff_methods(db: Session, firm_id: str | None, role: str) -> SignInMethods:
    """Sign-in methods for an account of `role` in `firm_id` (None: firm-less)."""
    governing = staff_firm_id(db, firm_id, role)
    if governing is None:
        return SignInMethods(entra=platform_directory(), local=False)
    return firm_sign_in_methods(db, governing)


def validate_settings(db: Session, firm_id: str, new: SignInSettings) -> SignInSettings:
    """`new` with its directory id normalized, or 422/409.

    At least one method stays on, Microsoft sign-in needs a directory, and in
    Entra mode the platform owner's firm keeps Microsoft sign-in through the
    platform directory: the master admin signs in through it.
    """
    tenant = normalize_tenant_id(new.tenant_id)
    if not new.entra_enabled and not new.local_enabled:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Keep at least one sign-in method on."
        )
    if new.entra_enabled and tenant is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Enter the Microsoft directory (tenant) ID to turn on Microsoft sign-in.",
        )
    platform = platform_directory()
    owner = platform_owner_firm(db) if platform is not None else None
    if platform is not None and owner is not None and owner.id == firm_id:
        if not new.entra_enabled:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The platform owner's firm must keep Microsoft sign-in: "
                "the platform administrator signs in with it.",
            )
        if tenant != platform.tenant_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "The platform owner's firm signs in through the platform directory "
                "(INSPRO_ENTRA_TENANT_ID).",
            )
    return replace(new, tenant_id=tenant)


def save_settings(db: Session, firm_id: str, new: SignInSettings) -> None:
    """Write both method rows for the firm (validated by the caller). No commit."""
    rows = _rows(db, firm_id)
    entra = rows.get(IDP_ENTRA) or IdentityProvider(broker_firm_id=firm_id, kind=IDP_ENTRA)
    entra.enabled = new.entra_enabled
    entra.entra_tenant_id = new.tenant_id
    entra.require_platform_mfa = new.require_platform_mfa
    local = rows.get(IDP_LOCAL) or IdentityProvider(broker_firm_id=firm_id, kind=IDP_LOCAL)
    local.enabled = new.local_enabled
    db.add_all([entra, local])
    db.flush()


def admin_consent_url(tenant_id: str | None, settings: Settings | None = None) -> str | None:
    """The link a broker's IT administrator opens to consent to the app registration."""
    settings = settings or get_settings()
    if not tenant_id or not settings.entra_client_id:
        return None
    return f"{AUTHORITY}/{tenant_id}/adminconsent?client_id={settings.entra_client_id}"
