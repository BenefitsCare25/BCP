"""Response security headers — applied to every response.

CSP is restrictive (`default-src 'self'`); the SPA is served from a different
origin in production so it never loads JS/CSS through this API. Override via
`INSPRO_CSP_OVERRIDE`.
"""
from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://login.microsoftonline.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, csp: str | None = None) -> None:
        super().__init__(app)
        self._csp = csp or os.environ.get("INSPRO_CSP_OVERRIDE", DEFAULT_CSP)
        # HSTS is meaningful only over HTTPS; App Service terminates TLS so
        # the inbound scheme is http inside the app. Decide at init from env
        # rather than per-request env reads.
        self._hsts_enabled = (
            os.environ.get("INSPRO_ENV", "dev").lower() in ("staging", "prod")
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = self._csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=()"
        )
        if self._hsts_enabled:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response
