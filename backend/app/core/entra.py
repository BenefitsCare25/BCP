"""Entra ID JWT validation.

`verify_entra_token(token, settings, jwks)` does the work. `jwks` is injected
so tests can supply a synthetic key set without hitting the network.
Production code calls the live JWKS URL via `PyJWKClient`.
"""
from __future__ import annotations

import logging
from typing import Any

import jwt
from cachetools import LRUCache
from jwt import PyJWKClient

from app.core.settings import Settings

logger = logging.getLogger(__name__)

# Allowable clock skew when checking `exp` / `nbf` / `iat`. Entra tokens
# occasionally arrive within a few seconds of issuance; 30s is safe.
_CLOCK_SKEW_SECONDS = 30

_MAX_JWKS_ENDPOINTS = 8

_jwks_client_cache: LRUCache[str, PyJWKClient] = LRUCache(maxsize=_MAX_JWKS_ENDPOINTS)


class EntraAuthError(RuntimeError):
    """Raised when a token cannot be verified."""


def _jwk_client(jwks_url: str) -> PyJWKClient:
    if jwks_url not in _jwks_client_cache:
        _jwks_client_cache[jwks_url] = PyJWKClient(jwks_url)
    return _jwks_client_cache[jwks_url]


def verify_entra_token(
    token: str,
    settings: Settings,
    jwks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a JWT against Entra's JWKS and return the decoded claims.

    When `jwks` is provided, signing keys are loaded from it directly
    (useful for tests). Otherwise the live JWKS URL is fetched via
    `PyJWKClient`.

    `audience` and `issuer` are required to be set in settings — verifying
    against ``None`` would silently disable those checks, which is how
    cross-tenant token replays succeed.
    """
    if not token:
        raise EntraAuthError("missing token")
    if not settings.entra_audience or not settings.entra_issuer:
        # Belt-and-braces; settings.py already refuses to build Settings
        # without these in entra mode.
        raise EntraAuthError("audience/issuer not configured")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise EntraAuthError(f"malformed token: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise EntraAuthError("token header missing kid")

    if jwks is not None:
        key_match = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if key_match is None:
            raise EntraAuthError(f"no matching JWK for kid={kid}")
        signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_match)
    else:
        try:
            signing_key = _jwk_client(settings.entra_jwks_url).get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise EntraAuthError(f"JWKS lookup failed: {exc}") from exc

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.entra_audience,
            issuer=settings.entra_issuer,
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

    return claims


def role_from_claims(claims: dict[str, Any], settings: Settings) -> str:
    """Map Entra group/role claims to an Inspro role.

    Order: explicit `roles` claim → group map → lowest-privilege fallback.
    Unrecognised role strings are dropped, never trusted verbatim. The fallback
    is `broker_viewer` (not `broker_admin`) so a misconfigured user can sign
    in but can't mutate data.
    """
    # Imported lazily to avoid a circular import (auth → entra → auth).
    from app.core.auth import ROLE_BROKER_VIEWER, VALID_ROLES

    if claims.get("_claim_names"):
        # Entra's overage indicator when a user is in >150 groups. We don't
        # follow the Graph reference — App Roles are the right answer.
        logger.warning(
            "Entra token uses group overage (_claim_names) — falling back to "
            "default role. Configure App Roles for users in >150 groups."
        )

    for r in claims.get("roles") or []:
        if isinstance(r, str) and r in VALID_ROLES:
            return r

    for gid in claims.get("groups", []) or []:
        mapped = settings.entra_group_role_map.get(gid)
        if mapped and mapped in VALID_ROLES:
            return mapped

    return ROLE_BROKER_VIEWER
