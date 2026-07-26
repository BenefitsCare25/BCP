"""Tenant-per-subdomain resolution.

The Host header IS the tenant selector for the credential (HR) and portal
(member) surfaces:

    broker.<base>          → broker surface (Entra); no tenant slug, firm from user row
    {slug}.hr.<base>       → HR surface;      tenant = clients.slug
    {slug}.portal.<base>   → portal surface;  tenant = clients.slug

This module is deliberately split into two layers:

- `parse_host()` / `TenantMiddleware` — pure, DB-free. The middleware stashes a
  `HostInfo(surface, slug)` on `request.state.host_info` for every request. It
  never rejects, never hits the DB (so `/health` and the broker API on
  `localhost` are untouched).
- `resolve_tenant_context()` — does the DB lookup, only for routes that ask for
  it. A subdomain naming an unknown or disabled tenant 404s; no subdomain at all
  (dev, direct API on localhost) yields `None`, so existing flows that resolve
  the client from the token keep working. Enforcement (token.client_id ==
  subdomain tenant) lives in the surface auth code (e.g. `require_hr_tenant` /
  `optional_hr_tenant` in `hr_auth`, `require_portal_tenant` in `portal_auth`),
  which pins the surface so the correct kill-switch flag is checked.

Slug rules (DNS label + reserved list) are enforced wherever a broker sets a
client's slug.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

SURFACE_BROKER = "broker"
SURFACE_HR = "hr"
SURFACE_PORTAL = "portal"

# Labels that must never be a tenant slug — they collide with platform hosts or
# are conventionally reserved. Kept lowercase; comparison is case-insensitive.
RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "broker", "hr", "portal", "www", "api", "admin", "app", "auth",
        "login", "signin", "sign-in", "static", "assets", "cdn", "mail",
        "smtp", "ftp", "ns", "ns1", "ns2", "mx", "test", "staging", "stg",
        "dev", "demo", "internal", "system", "root", "status", "health",
        "inspro", "support", "help", "docs",
    }
)

# A single DNS label: 1-63 chars, lowercase alnum + internal hyphens, no
# leading/trailing/double hyphen.
_SLUG_RE = re.compile(r"^(?!-)(?!.*--)[a-z0-9-]{1,63}(?<!-)$")


class SlugError(ValueError):
    """Raised when a proposed client slug is malformed or reserved."""


def normalize_slug(raw: str) -> str:
    """Lowercase + trim a proposed slug. Does not validate — call `validate_slug`."""
    return (raw or "").strip().lower()


def validate_slug(raw: str) -> str:
    """Return the normalized slug or raise `SlugError`.

    A slug is a single DNS label so it can front a wildcard-TLS subdomain.
    """
    slug = normalize_slug(raw)
    if not slug:
        raise SlugError("Slug must not be empty.")
    if not _SLUG_RE.match(slug):
        raise SlugError(
            "Slug must be 1-63 chars: lowercase letters, digits and single "
            "hyphens, no leading/trailing/double hyphen."
        )
    if slug in RESERVED_SLUGS:
        raise SlugError(f"'{slug}' is reserved and cannot be used as a slug.")
    return slug


def _label_ok(label: str) -> bool:
    return bool(_SLUG_RE.match(label))


@dataclass(frozen=True)
class HostInfo:
    """What the Host header alone tells us — no DB involved."""

    surface: str  # SURFACE_BROKER | SURFACE_HR | SURFACE_PORTAL
    slug: str | None  # tenant slug for hr/portal; None for broker


def parse_host(host: str, base_domain: str) -> HostInfo | None:
    """Map a Host header to a `HostInfo`, or None if it isn't a platform host.

    Accepts the configured base domain AND `localhost` (dev convenience:
    `acme.portal.localhost` behaves like `acme.portal.<base>`). Port and a
    trailing dot are stripped. Unknown hosts (bare `localhost`, `127.0.0.1`,
    the App Service default host) return None — callers treat that as "no
    subdomain binding".
    """
    host = (host or "").split(":", 1)[0].strip().lower().rstrip(".")
    if not host:
        return None
    base_domain = (base_domain or "").strip().lower().strip(".")
    for base in (base_domain, "localhost"):
        if not base:
            continue
        suffix = f".{base}"
        if host == f"{SURFACE_BROKER}{suffix}":
            return HostInfo(SURFACE_BROKER, None)
        for surface in (SURFACE_PORTAL, SURFACE_HR):
            tail = f".{surface}{suffix}"
            if host.endswith(tail):
                label = host[: -len(tail)]
                return HostInfo(surface, label) if _label_ok(label) else None
    return None


def resolve_host_info(
    request: Request, surface: str, slug_header: str | None
) -> HostInfo | None:
    """`HostInfo` for `surface`, falling back to an explicit tenant-slug header.

    A real Host header always wins. The `X-Inspro-Tenant-Slug` fallback applies
    when the request carries no recognisable platform host, and only when:

    - `INSPRO_TENANT_MODE=header` — a single-host deployment. With no custom
      domain there are no tenant subdomains to parse (the App Service default
      `*.azurewebsites.net` cannot have them), so the SPA names the tenant.
    - or the environment isn't prod — so both surfaces stay testable on
      localhost, which is the behaviour this helper replaced.

    Security note: the header SELECTS a tenant, it does not authorise one.
    `get_current_hr_user` still rejects a token whose `cid` doesn't match the
    resolved tenant, so a forged header only changes which tenant's sign-in is
    being attempted — exactly what an attacker could already do by choosing a
    subdomain. What is lost versus subdomain mode is the defence-in-depth of a
    binding the client cannot pick, so prefer `subdomain` once DNS exists.
    """
    host_info: HostInfo | None = getattr(request.state, "host_info", None)
    if host_info is not None:
        return host_info

    slug = normalize_slug(slug_header or "")
    if not slug or not _label_ok(slug):
        return None

    from app.core.settings import get_settings  # lazy: avoid import cycle

    settings = get_settings()
    if settings.tenant_mode == "header" or settings.env != "prod":
        return HostInfo(surface, slug)
    return None


@dataclass(frozen=True)
class TenantContext:
    """A resolved tenant from a subdomain — the DB row exists and is enabled."""

    surface: str
    slug: str
    client_id: str
    broker_firm_id: str


def resolve_tenant_context(host_info: HostInfo | None, db: Session) -> TenantContext | None:
    """Resolve a `HostInfo` to a live tenant, or 404 for a bad/disabled slug.

    - broker surface / no host info → None (no tenant binding).
    - hr/portal with a slug → look up `clients.slug`; 404 if missing or the
      surface's kill-switch is off. Returns a `TenantContext` otherwise.
    """
    if host_info is None or host_info.surface == SURFACE_BROKER or not host_info.slug:
        return None

    from app.models import Client  # lazy: avoid import cost at module load

    client = db.execute(
        select(Client).where(Client.slug == host_info.slug)
    ).scalar_one_or_none()
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")
    enabled = (
        client.portal_enabled
        if host_info.surface == SURFACE_PORTAL
        else client.hr_enabled
    )
    if not enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown tenant.")
    return TenantContext(
        surface=host_info.surface,
        slug=host_info.slug,
        client_id=client.id,
        broker_firm_id=client.broker_firm_id,
    )


class TenantMiddleware(BaseHTTPMiddleware):
    """Parse the Host header once per request and stash `HostInfo` on state.

    Pure and DB-free — resolution + rejection happen later in the surface tenant
    dependencies (`require_hr_tenant` / `optional_hr_tenant` / etc.) for the
    routes that need a tenant.
    """

    def __init__(self, app: ASGIApp, base_domain: str) -> None:
        super().__init__(app)
        self._base_domain = base_domain

    async def dispatch(self, request: Request, call_next):
        request.state.host_info = parse_host(
            request.headers.get("host", ""), self._base_domain
        )
        return await call_next(request)
