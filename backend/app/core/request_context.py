"""Per-request correlation ID + middleware that surfaces it everywhere.

The middleware:
  1. Reads the inbound `X-Request-ID` header (set by Azure Front Door / App
     Service) or generates a fresh UUID.
  2. Stores it in a `ContextVar` so audit-log writes and structured-logging
     records can include it without explicit plumbing — together with the
     caller's IP and user agent, so every audit row carries them even when the
     writing code never sees the request.
  3. Echoes it back as a response header so callers can correlate.

Use `get_request_id()` / `get_client_ip()` / `get_user_agent()` from anywhere
in the request lifecycle. Outside a request (e.g. background jobs) they return
`None`.
"""
from __future__ import annotations

import ipaddress
import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
_user_agent_var: ContextVar[str | None] = ContextVar("user_agent", default=None)

_REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id() -> str | None:
    return _request_id_var.get()


def get_client_ip() -> str | None:
    """The current request's caller IP (see `client_ip`), or None outside one."""
    return _client_ip_var.get()


def get_user_agent() -> str | None:
    """The current request's User-Agent header, or None outside a request."""
    return _user_agent_var.get()


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


# Set by Azure Front Door to the address of the TCP connection it received.
# Unlike `X-Forwarded-For` (and Front Door's `{client_ip}`), a client cannot
# choose it.
FRONT_DOOR_SOCKET_IP_HEADER = "x-azure-socketip"


def _edge_client_ip(request: Request) -> str | None:
    """The browser's address when the request provably came through our Front
    Door profile (its `X-Azure-FDID`); None otherwise.

    Trustworthy once the origin lock admits only Front Door (see the Front
    Door runbook): before that, anyone reaching the app directly could send
    both headers, exactly as they could choose the edge host.
    """
    from app.core.settings import get_settings
    from app.core.tenant_resolution import FRONT_DOOR_ID_HEADER

    profile = get_settings().front_door_id
    if not profile:
        return None
    if (request.headers.get(FRONT_DOOR_ID_HEADER) or "").strip().lower() != profile:
        return None
    raw = (request.headers.get(FRONT_DOOR_SOCKET_IP_HEADER) or "").strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """Caller IP for audit rows and rate-limit buckets.

    Behind Front Door, the socket address Front Door saw. Otherwise the peer
    the ASGI server reports, which is the forwarded client only when the
    server's trusted-proxy policy says so. None behind a proxy that strips it.
    """
    edge = _edge_client_ip(request)
    if edge is not None:
        return edge
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _coerce_inbound_id(request.headers.get(_REQUEST_ID_HEADER))
        token = _request_id_var.set(request_id)
        ip_token = _client_ip_var.set(client_ip(request))
        agent_token = _user_agent_var.set(user_agent(request))
        try:
            response = await call_next(request)
        finally:
            _user_agent_var.reset(agent_token)
            _client_ip_var.reset(ip_token)
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
