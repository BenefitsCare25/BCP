"""Origin checks for authentication operations using browser cookies.

Same-origin is judged against the request's own `Host` header — the host the
browser addressed. Behind Front Door that header has already been rewritten to
the public host by `tenant_resolution.FirmResolutionMiddleware` (from the
edge-set `X-Inspro-Edge-Host`, trusted only with the profile's id), so every
firm domain works without being listed in `INSPRO_CORS_ORIGINS`. Client-supplied
forwarding headers (`X-Forwarded-Host` and the like) are never consulted: they
would let a cross-site page name any host it liked.
"""
from urllib.parse import SplitResult, urlsplit

from fastapi import HTTPException, Request, status

from app.core.settings import get_settings

_DEFAULT_PORTS = {"http": ":80", "https": ":443"}


def _refused(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)


def _same_origin(origin: SplitResult, request: Request, *, prod: bool) -> bool:
    """Whether `origin` is the host this request was addressed to.

    TLS may end before the app (the edge, App Service), so the request's own
    scheme can read `http` for a page the browser loaded over `https`; an https
    origin on the same host is accepted for that reason. Production accepts
    https only — a plaintext page on the same name is not the site.
    """
    host = request.headers.get("host", "").strip().lower()
    if not host or not origin.netloc:
        return False
    authority = origin.netloc.lower()
    default_port = _DEFAULT_PORTS.get(origin.scheme, "")
    if default_port and host.endswith(default_port):
        host = host.removesuffix(default_port)
    if authority != host:
        return False
    if prod:
        return origin.scheme == "https"
    return origin.scheme in (request.url.scheme, "https")


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise _refused("Cross-site authentication is not allowed.")
    if origin is None:
        return  # Non-browser clients do not carry an Origin header.
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise _refused("Invalid authentication origin.") from None
    settings = get_settings()
    if parsed.scheme not in {"http", "https"} or not (
        _same_origin(parsed, request, prod=settings.env == "prod")
        or origin.rstrip("/") in settings.cors_origins
    ):
        raise _refused("Cross-site authentication is not allowed.")
