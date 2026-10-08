"""Response security headers — applied to every response.

Registered as the OUTERMOST middleware (see `main.create_app`), so responses
produced by the other middleware — rate-limit 429s, CORS preflights, upload
413s — carry them too. An unhandled exception is answered by Starlette's
`ServerErrorMiddleware`, outside every middleware, so its handler stamps the
same headers with `apply_security_headers`.

CSP is restrictive (`default-src 'self'`); the SPA is served from a different
origin in production so it never loads JS/CSS through this API. Override via
`INSPRO_CSP_OVERRIDE`.
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.settings import get_settings

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    # Company email branding permits HTTPS logos. srcdoc email previews inherit
    # this policy; their own stricter policy cannot relax a parent restriction.
    # Scripts, connections and framing remain limited by their own directives.
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://login.microsoftonline.com; "
    # Claim review renders authenticated PDF bytes through an object URL. Keep
    # framing limited to same-origin content and blobs created by this app.
    "frame-src 'self' blob: https://login.microsoftonline.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


def _never_store(response: Response) -> bool:
    """A response that issues a session cookie or is a file download — the
    auth tokens and personal-data exports — unless its endpoint chose a cache
    policy itself."""
    if "cache-control" in response.headers:
        return False
    disposition = response.headers.get("content-disposition", "")
    return "set-cookie" in response.headers or disposition.lower().startswith("attachment")


def _deployment_csp() -> str:
    return os.environ.get("INSPRO_CSP_OVERRIDE", DEFAULT_CSP)


def _deployment_hsts() -> bool:
    # HSTS is meaningful only over HTTPS; App Service terminates TLS so the
    # inbound scheme is http inside the app. Decided from the resolved
    # environment (which maps "production" → "prod" and "stg" → "staging")
    # rather than the raw variable.
    return get_settings().env in ("staging", "prod")


def apply_security_headers(
    response: Response, *, csp: str | None = None, hsts: bool | None = None
) -> Response:
    """Stamp the security headers on ``response`` and return it.

    ``csp`` and ``hsts`` default to the deployment's policy; the middleware
    resolves them once at start-up and passes them in.
    """
    response.headers["Content-Security-Policy"] = csp or _deployment_csp()
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "geolocation=(), microphone=(), camera=(), payment=()"
    )
    if hsts is None:
        hsts = _deployment_hsts()
    if hsts:
        response.headers["Strict-Transport-Security"] = (
            "max-age=63072000; includeSubDomains; preload"
        )
    if _never_store(response):
        response.headers["Cache-Control"] = "no-store"
    return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, csp: str | None = None) -> None:
        super().__init__(app)
        # Resolved at init rather than per request.
        self._csp = csp or _deployment_csp()
        self._hsts_enabled = _deployment_hsts()

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        return apply_security_headers(response, csp=self._csp, hsts=self._hsts_enabled)
