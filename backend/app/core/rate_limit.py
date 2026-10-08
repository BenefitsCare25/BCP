"""SlowAPI rate-limit setup.

Rate limits apply to cheap-DOS-vector endpoints — placement-slip parse,
employee/dependant upload, and matching-run — which all do meaningful DB or
AI work. Buckets use the trusted ASGI client address, not caller-supplied
tenant headers. Limits and shared storage are tunable via environment variables.
"""
from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded


def _key_func(request: Request) -> str:
    """Use the ASGI peer (resolved only by the server's trusted-proxy policy),
    or behind Front Door the socket address it saw (`request_context.client_ip`).

    Never let anonymous callers select a bucket with tenant or forwarding headers.
    """
    from app.core.request_context import client_ip

    return f"ip:{client_ip(request) or 'unknown'}"


limiter = Limiter(
    key_func=_key_func,
    default_limits=[os.environ.get("INSPRO_RATE_LIMIT_DEFAULT", "120/minute")],
    enabled=os.environ.get("INSPRO_RATE_LIMIT_ENABLED", "1") != "0",
    # Shared storage so the limit holds across multiple gunicorn workers /
    # App Service instances. Falls back to in-memory when unset (single-process
    # dev). The storage URI is the standard Redis URL.
    storage_uri=os.environ.get("INSPRO_REDIS_URL", "memory://"),
)


__all__ = ["RateLimitExceeded", "limiter"]
