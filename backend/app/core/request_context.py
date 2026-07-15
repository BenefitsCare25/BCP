"""Per-request correlation ID + middleware that surfaces it everywhere.

The middleware:
  1. Reads the inbound `X-Request-ID` header (set by Azure Front Door / App
     Service) or generates a fresh UUID.
  2. Stores it in a `ContextVar` so audit-log writes and structured-logging
     records can include it without explicit plumbing.
  3. Echoes it back as a response header so callers can correlate.

Use `get_request_id()` from anywhere in the request lifecycle. Outside a
request (e.g. background jobs) it returns `None`.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str | None:
    return _request_id_var.get()


def _coerce_inbound_id(raw: str | None) -> str:
    """Trust the upstream-provided ID only if it looks like a UUID or short
    opaque token — otherwise generate one. Protects against header injection
    that lands an attacker-controlled string in our logs."""
    if not raw:
        return uuid.uuid4().hex
    candidate = raw.strip()
    if not (1 <= len(candidate) <= 200):
        return uuid.uuid4().hex
    # Allow alphanumerics, dashes, underscores only — drops control chars.
    if not all(c.isalnum() or c in "-_" for c in candidate):
        return uuid.uuid4().hex
    return candidate


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = _coerce_inbound_id(request.headers.get(_REQUEST_ID_HEADER))
        token = _request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class RequestIDLogFilter(logging.Filter):
    """Attach the current request_id to each LogRecord so format strings
    can reference `%(request_id)s`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True


def install_log_filter() -> None:
    """Add the request-ID filter to the root logger once."""
    root = logging.getLogger()
    if not any(isinstance(f, RequestIDLogFilter) for f in root.filters):
        root.addFilter(RequestIDLogFilter())
