"""Which broker firm a request belongs to, resolved from the host it arrived on.

Every broker firm is a white-label tenant with its own hostnames
(`tenant_domains`). The platform owner's firm (Inspro's own) is served on the
platform hosts (`Settings.platform_hosts`), which also carry the master-admin
console. This module turns a request's host into a `FirmContext` once, in
middleware, and stores it on `request.state.firm`.

**The host selects a firm; it never authorises anything.** Sessions carry their
own firm, and the auth dependencies refuse a session whose firm differs from
the request's — so a token minted on broker A's domain is useless on broker B's.

Behind Azure Front Door (`Settings.front_door_id` set) the app does not trust
the `Host` header: the request must carry the profile's `X-Azure-FDID`, and the
public host comes from `X-Inspro-Edge-Host`, which a Front Door rule set
overwrites on every request. The middleware then rewrites the ASGI `host`
header to that public host, so URLs, origin checks and redirects downstream see
the domain the browser used.

A firm without a domain of its own is reachable on its neutral fallback address,
`<firm-slug>.<INSPRO_FIRM_FALLBACK_DOMAIN>`. `public_origin` turns a firm into
the origin its emailed links use, from the same data.

Outside production an unresolvable host passes through with no firm (local
tools, tests with several firms); in production it is a 404.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.settings import Settings, get_settings
from app.models.client import FIRM_STATUS_ACTIVE, FIRM_STATUS_SUSPENDED, BrokerFirm
from app.models.platform import (
    DOMAIN_STATUS_ACTIVE,
    DOMAIN_SURFACE_ALL,
    DOMAIN_SURFACE_CLIENT,
    DOMAIN_SURFACE_STAFF,
    TenantDomain,
)

logger = logging.getLogger(__name__)

EDGE_HOST_HEADER = "x-inspro-edge-host"
FRONT_DOOR_ID_HEADER = "x-azure-fdid"
# Probes answer without a firm: App Service and the worker call them directly.
EXEMPT_PATHS = frozenset({"/health", "/readiness", "/readyz"})
CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class FirmContext:
    """The broker firm a request was addressed to."""

    firm_id: str
    firm_slug: str | None
    surface: str  # models.platform.DOMAIN_SURFACE_*
    is_platform_host: bool
    database_key: str
    status: str
    host: str

    def serves(self, surface: str) -> bool:
        return self.surface in (DOMAIN_SURFACE_ALL, surface)


class EdgeRejected(Exception):
    """A request that did not come through the configured edge correctly."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_host(raw: str | None) -> str:
    """Lowercase host without port or trailing dot ('' when absent)."""
    host = (raw or "").strip().lower()
    if host.startswith("["):  # IPv6 literal: keep the address, drop the port
        return host.split("]", 1)[0] + "]"
    return host.split(":", 1)[0].rstrip(".")


def effective_host(headers: Mapping[str, str], settings: Settings) -> str:
    """The public host the browser used. Raises `EdgeRejected` off-edge."""
    if not settings.front_door_id:
        return normalize_host(headers.get("host"))
    if (headers.get(FRONT_DOOR_ID_HEADER) or "").strip().lower() != settings.front_door_id:
        raise EdgeRejected(
            status.HTTP_403_FORBIDDEN, "edge_required", "Requests must come through the edge."
        )
    edge = normalize_host(headers.get(EDGE_HOST_HEADER))
    if not edge:
        raise EdgeRejected(
            status.HTTP_400_BAD_REQUEST, "edge_host_missing", "The edge did not name the host."
        )
    return edge


def _context(firm: BrokerFirm, host: str, surface: str, *, platform: bool) -> FirmContext:
    return FirmContext(
        firm_id=firm.id,
        firm_slug=firm.slug,
        surface=surface,
        is_platform_host=platform,
        database_key=firm.database_key,
        status=firm.status,
        host=host,
    )


def platform_owner_firm(db: Session) -> BrokerFirm | None:
    """The firm flagged as platform owner, else the sole firm if only one exists."""
    owner = db.execute(
        select(BrokerFirm).where(BrokerFirm.is_platform_owner.is_(True))
    ).scalar_one_or_none()
    if owner is not None:
        return owner
    firms = db.execute(select(BrokerFirm).limit(2)).scalars().all()
    return firms[0] if len(firms) == 1 else None


def _legacy_platform_host(host: str, settings: Settings) -> bool:
    """`broker.<base>`, `{slug}.hr.<base>`, `{slug}.portal.<base>` in subdomain mode.

    That shape predates white-labelling and belongs to the platform owner.
    """
    if settings.tenant_mode != "subdomain":
        return False
    from app.core.tenancy_host import parse_host  # lazy: avoid an import cycle

    return parse_host(host, settings.base_domain) is not None


def _fallback_firm(db: Session, host: str, label: str) -> FirmContext | None:
    """`<firm-slug>.<fallback domain>`: an active firm's neutral address.

    Definitive for every host under the fallback domain — an unknown or inactive
    label resolves to no firm, never to the platform owner.
    """
    if "." in label:
        return None
    firm = db.execute(
        select(BrokerFirm).where(BrokerFirm.slug == label, BrokerFirm.status == FIRM_STATUS_ACTIVE)
    ).scalar_one_or_none()
    return _context(firm, host, DOMAIN_SURFACE_ALL, platform=False) if firm else None


def lookup_firm(db: Session, host: str, settings: Settings) -> FirmContext | None:
    """Resolve a normalized host to its firm, uncached."""
    if host in settings.platform_hosts or _legacy_platform_host(host, settings):
        owner = platform_owner_firm(db)
        return _context(owner, host, DOMAIN_SURFACE_ALL, platform=True) if owner else None
    row = db.execute(
        select(TenantDomain, BrokerFirm)
        .join(BrokerFirm, BrokerFirm.id == TenantDomain.broker_firm_id)
        .where(TenantDomain.hostname == host, TenantDomain.status == DOMAIN_STATUS_ACTIVE)
    ).first()
    if row is not None:
        domain, firm = row
        return _context(firm, host, domain.surface, platform=False)
    fallback = settings.firm_fallback_domain
    if fallback and host.endswith("." + fallback):
        return _fallback_firm(db, host, host.removesuffix("." + fallback))
    if settings.env != "prod" and host.endswith(".localhost"):
        firm = db.execute(
            select(BrokerFirm).where(BrokerFirm.slug == host.removesuffix(".localhost"))
        ).scalar_one_or_none()
        if firm is not None:
            return _context(firm, host, DOMAIN_SURFACE_ALL, platform=False)
    if settings.env != "prod":
        owner = platform_owner_firm(db)
        return _context(owner, host, DOMAIN_SURFACE_ALL, platform=True) if owner else None
    return None


_cache: dict[str, tuple[float, FirmContext | None]] = {}
_cache_lock = threading.Lock()


def invalidate_firm_cache() -> None:
    """Forget every cached host. Call after changing firms or domains."""
    with _cache_lock:
        _cache.clear()


def resolve_firm(host: str) -> FirmContext | None:
    """Cached `lookup_firm` with its own short-lived session (blocking)."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(host)
    if hit is not None and hit[0] > now:
        return hit[1]
    from app.db.session import SessionLocal  # lazy: tests rebind the engine

    with SessionLocal() as db:
        ctx = lookup_firm(db, host, get_settings())
    with _cache_lock:
        _cache[host] = (now + CACHE_TTL_SECONDS, ctx)
    return ctx


def request_firm(request: Request) -> FirmContext | None:
    """The firm the middleware resolved for this request, if any."""
    ctx = getattr(request.state, "firm", None)
    return ctx if isinstance(ctx, FirmContext) else None


def require_request_firm(request: Request) -> FirmContext:
    """Dependency: the request's firm, or 404 when the host names none."""
    ctx = request_firm(request)
    if ctx is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, {
            "code": "unknown_site", "message": "This site is not available.",
        })
    return ctx


def require_platform_host(request: Request) -> FirmContext:
    """Dependency: the request arrived on a platform host (master console)."""
    ctx = request_firm(request)
    if ctx is None or not ctx.is_platform_host:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return ctx


def refuse_unserved_surface(ctx: FirmContext | None, surface: str) -> None:
    """404 when the request's host does not serve `surface` at all.

    A firm may give its staff and its clients separate hostnames; the HR and
    employee portals do not exist on a staff-only host, nor the broker app on a
    client-only one. No firm context (local tools, tests) serves everything.
    """
    if ctx is not None and not ctx.serves(surface):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def require_staff_surface(request: Request) -> None:
    """Dependency: the host serves the broker staff app."""
    refuse_unserved_surface(request_firm(request), DOMAIN_SURFACE_STAFF)


def require_client_surface(request: Request) -> None:
    """Dependency: the host serves the HR and employee portals."""
    refuse_unserved_surface(request_firm(request), DOMAIN_SURFACE_CLIENT)


class FirmOriginUnavailable(Exception):
    """A firm has no web address a link could point at yet.

    Raised by `public_origin`. Whoever is about to mail a link must not send:
    broker-triggered sends answer `firm_origin_unavailable_error()`, workers log
    and keep the item for a later retry.
    """

    code = "firm_origin_unavailable"
    message = "This broker has no active web address yet."

    def __init__(self, firm_id: str | None) -> None:
        super().__init__(f"Broker firm {firm_id} has no active web address.")
        self.firm_id = firm_id


def firm_origin_unavailable_error() -> HTTPException:
    """409 for a broker-triggered send whose firm has no address yet."""
    return HTTPException(status.HTTP_409_CONFLICT, {
        "code": FirmOriginUnavailable.code, "message": FirmOriginUnavailable.message,
    })


def _development_origin(settings: Settings, firm: BrokerFirm | None) -> str:
    """Dev only: `<firm-slug>.localhost` on the dev server, which `lookup_firm`
    resolves to that firm; the frontend origin itself for a firm with no slug."""
    origin = settings.frontend_origin.rstrip("/")
    parts = urlsplit(origin)
    if firm is None or not firm.slug or parts.hostname not in ("localhost", "127.0.0.1"):
        return origin
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{firm.slug}.localhost{port}"


def public_origin(db: Session, firm_id: str, surface: str) -> str:
    """The origin (`https://host`, no trailing slash) for links into a firm.

    `surface` is `client` for HR and employee links, `staff` for broker links.
    In order: the firm's active primary domain serving the surface, any other
    active domain serving it, `https://<firm-slug>.<fallback>` when
    `INSPRO_FIRM_FALLBACK_DOMAIN` is set, and — for the platform owner's firm
    only — `INSPRO_FRONTEND_ORIGIN`. Otherwise there is nowhere a link could
    point: raises `FirmOriginUnavailable`, and the caller must not send. Local
    development (`INSPRO_ENV=dev`) falls back to `_development_origin` instead,
    so mail works before any domain exists.
    """
    domains = db.execute(
        select(TenantDomain.hostname, TenantDomain.surface, TenantDomain.is_primary).where(
            TenantDomain.broker_firm_id == firm_id,
            TenantDomain.status == DOMAIN_STATUS_ACTIVE,
            TenantDomain.surface.in_((surface, DOMAIN_SURFACE_ALL)),
        )
    ).all()
    if domains:
        hostname, _, _ = min(
            domains, key=lambda row: (not row[2], row[1] != surface, row[0])
        )
        return f"https://{hostname}"
    settings = get_settings()
    firm = db.get(BrokerFirm, firm_id)
    if firm is not None and firm.slug and settings.firm_fallback_domain:
        return f"https://{firm.slug}.{settings.firm_fallback_domain}"
    owner = platform_owner_firm(db)
    if firm is not None and owner is not None and owner.id == firm.id:
        return settings.frontend_origin.rstrip("/")
    if settings.env == "dev":
        return _development_origin(settings, firm)
    raise FirmOriginUnavailable(firm_id)


class FirmResolutionMiddleware:
    """Resolve the request's firm once and refuse requests that name none.

    Pure ASGI so it runs before routing and can rewrite the host header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "") in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        try:
            host = effective_host(headers, settings)
        except EdgeRejected as exc:
            await _refuse(scope, send, exc.status_code, exc.code, exc.message)
            return
        if settings.front_door_id:
            scope["headers"] = [
                (k, host.encode("latin-1") if k == b"host" else v) for k, v in scope["headers"]
            ]
        ctx = await run_in_threadpool(resolve_firm, host)
        if ctx is None and settings.env == "prod":
            await _refuse(scope, send, 404, "unknown_site", "This site is not available.")
            return
        if ctx is not None and ctx.status == FIRM_STATUS_SUSPENDED and not ctx.is_platform_host:
            await _refuse(
                scope, send, 403, "firm_suspended", "This service is currently unavailable."
            )
            return
        scope.setdefault("state", {})["firm"] = ctx
        await self.app(scope, receive, send)


async def _refuse(scope: Scope, send: Send, status_code: int, code: str, message: str) -> None:
    path = str(scope.get("path", ""))
    if path.startswith("/api/"):
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        content_type = b"application/json"
    else:
        body = (
            "<!doctype html><meta charset=utf-8><title>Unavailable</title>"
            f"<p>{message}</p>"
        ).encode()
        content_type = b"text/html; charset=utf-8"
    await send({
        "type": "http.response.start",
        "status": status_code,
        "headers": [(b"content-type", content_type), (b"cache-control", b"no-store")],
    })
    await send({"type": "http.response.body", "body": body})


def client_origin(db: Session, firm_id: str | None) -> str | None:
    """The firm's address for HR and employee links, or None when it has none.

    Broker screens build set-password and sign-in links from this rather than
    their own location: a broker on a staff-only host would otherwise hand out
    a link to a host where the HR and employee portals don't exist.
    """
    if not firm_id:
        return None
    try:
        return public_origin(db, firm_id, DOMAIN_SURFACE_CLIENT)
    except FirmOriginUnavailable:
        return None
