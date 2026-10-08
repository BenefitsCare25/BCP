"""Entra ID JWT validation against one broker firm's own directory.

Every broker firm that offers Microsoft sign-in names its Entra directory
(tenant). The platform's single app registration (`INSPRO_ENTRA_CLIENT_ID`) is
shared by every firm, so a token is accepted only when it was issued by the
directory of the firm whose host the request arrived on: its `tid` equals that
directory, its issuer is exactly that directory's v2 issuer, and its signature
verifies against that directory's own signing keys.

`verify_entra_token(token, settings, tenant_id=..., jwks=None)` does the work.
`jwks` is injected so tests can supply a synthetic key set without hitting the
network; production fetches the directory's JWKS via `PyJWKClient`.
"""
from __future__ import annotations

import re
import threading
from typing import Any

import jwt
from cachetools import LRUCache
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from jwt import PyJWKClient

from app.core.settings import Settings

AUTHORITY = "https://login.microsoftonline.com"
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Allowable clock skew when checking `exp` / `nbf` / `iat`. Entra tokens
# occasionally arrive within a few seconds of issuance; 30s is safe.
_CLOCK_SKEW_SECONDS = 30

# One key client per directory in use. An app registration holds at most 256
# redirect URIs, which bounds the broker staff hosts and so the directories.
_MAX_JWKS_ENDPOINTS = 256

_jwks_client_cache: LRUCache[str, PyJWKClient] = LRUCache(maxsize=_MAX_JWKS_ENDPOINTS)
_jwks_cache_lock = threading.Lock()


class EntraAuthError(RuntimeError):
    """Raised when a token cannot be verified."""


def is_directory_id(value: str | None) -> bool:
    """Whether `value` is a lowercase, hyphenated directory (tenant) GUID."""
    return bool(value) and _GUID.match(value or "") is not None


def issuer_for(tenant_id: str) -> str:
    return f"{AUTHORITY}/{tenant_id}/v2.0"


def jwks_url_for(tenant_id: str) -> str:
    return f"{AUTHORITY}/{tenant_id}/discovery/v2.0/keys"


def _jwk_client(jwks_url: str) -> PyJWKClient:
    # Request handlers run in a thread pool; cachetools caches are not
    # thread-safe on their own.
    with _jwks_cache_lock:
        client = _jwks_client_cache.get(jwks_url)
        if client is None:
            client = PyJWKClient(jwks_url)
            _jwks_client_cache[jwks_url] = client
        return client


def _signing_key(token: str, tenant_id: str, jwks: dict[str, Any] | None) -> Any:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise EntraAuthError(f"malformed token: {exc}") from exc
    kid = header.get("kid")
    if not kid:
        raise EntraAuthError("token header missing kid")
    if jwks is None:
        try:
            return _jwk_client(jwks_url_for(tenant_id)).get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise EntraAuthError(f"JWKS lookup failed: {exc}") from exc
    key_match = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key_match is None:
        raise EntraAuthError(f"no matching JWK for kid={kid}")
    signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_match)
    if isinstance(signing_key, RSAPrivateKey):
        signing_key = signing_key.public_key()
    return signing_key


def verify_entra_token(
    token: str,
    settings: Settings,
    *,
    tenant_id: str,
    jwks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a delegated access token issued by directory `tenant_id`.

    `tenant_id` is the directory of the firm the request is for, never a value
    taken from the token. The audience and the requesting client (`azp`) are
    the platform app registration's. Returns the decoded claims.
    """
    if not token:
        raise EntraAuthError("missing token")
    if not settings.entra_audience or not settings.entra_client_id:
        # Belt-and-braces; settings.py refuses to build Settings without these
        # in entra mode. Verifying against an empty audience would disable it.
        raise EntraAuthError("audience/client not configured")
    if not is_directory_id(tenant_id):
        raise EntraAuthError("directory id is not a GUID")

    signing_key = _signing_key(token, tenant_id, jwks)
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            issuer=issuer_for(tenant_id),
            leeway=_CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "nbf"]},
        )
    except jwt.InvalidAudienceError as exc:
        raise EntraAuthError(f"audience mismatch: {exc}") from exc
    except jwt.InvalidIssuerError as exc:
        raise EntraAuthError(f"issuer mismatch: {exc}") from exc
    except jwt.ExpiredSignatureError as exc:
        raise EntraAuthError("token expired") from exc
    except jwt.ImmatureSignatureError as exc:
        raise EntraAuthError("token not yet valid (nbf)") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise EntraAuthError(f"missing required claim: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise EntraAuthError(f"invalid token: {exc}") from exc

    if claims.get("tid") != tenant_id:
        raise EntraAuthError("tenant mismatch")
    if claims.get("azp") != settings.entra_client_id:
        raise EntraAuthError("unauthorized client application")
    scopes = claims.get("scp")
    if not isinstance(scopes, str) or "access_as_user" not in scopes.split():
        raise EntraAuthError("missing delegated API permission")
    if claims.get("idtyp") == "app":
        raise EntraAuthError("app-only tokens are not accepted")
    if not isinstance(claims.get("oid"), str) or not claims["oid"]:
        raise EntraAuthError("missing immutable user identifier")
    return claims
