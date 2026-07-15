"""SlowAPI rate-limit setup.

Rate limits apply to cheap-DOS-vector endpoints — placement-slip parse,
employee/dependant upload, and matching-run — which all do meaningful DB or
AI work. Per-client keys so one client's misbehaviour can't choke another.
Tunable via env vars.
"""
from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded


def _key_func(request: Request) -> str:
    """Rate-limit key: per active client when known, else per-IP.

    The frontend sends the active tenant as `X-Inspro-Client` on every request,
    so bucketing on it keeps one tenant's bursts from consuming another's quota
    (the limit is enforced after auth validates the client, so a spoofed value
    only ever throttles that same value's bucket). Requests without the header
    (anonymous / pre-auth) fall back to the originating IP.

    App Service / Front Door forward the real client IP as the first
    comma-separated entry in X-Forwarded-For; SlowAPI's stock
    `get_remote_address` reads only the immediate peer (the LB), which would
    turn the default into a global cap.
    """
    client = request.headers.get("x-inspro-client", "").strip()
    if client:
        return f"client:{client}"
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        ip = fwd.split(",")[0].strip()
        if ip:
            return f"ip:{ip}"
    # Direct peer fallback (e.g. dev server).
    if request.client and request.client.host:
        return f"ip:{request.client.host}"
    return "ip:unknown"


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
