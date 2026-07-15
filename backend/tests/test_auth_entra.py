"""Entra JWT validation — synthetic key + token, no network.

Generates an RSA keypair, signs a JWT, builds the corresponding JWKS, then
runs it through `verify_entra_token` to confirm signature, audience, issuer,
and expiry are all checked.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.entra import EntraAuthError, role_from_claims, verify_entra_token
from app.core.settings import Settings

KID = "test-kid-1"
AUDIENCE = "api://inspro"
ISSUER = "https://login.microsoftonline.com/tenant-x/v2.0"


@pytest.fixture(scope="module")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64u(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture(scope="module")
def jwks(rsa_keypair) -> dict[str, Any]:
    pub = rsa_keypair.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": KID,
                "alg": "RS256",
                "n": _b64u(pub.n),
                "e": _b64u(pub.e),
            }
        ]
    }


def _sign(rsa_keypair, claims: dict[str, Any]) -> str:
    pem = rsa_keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="entra",
        entra_tenant_id="tenant-x",
        entra_client_id=AUDIENCE,
        entra_audience=AUDIENCE,
        entra_issuer=ISSUER,
        entra_jwks_url="https://example.test/.well-known/jwks.json",
        entra_group_role_map={"00000000-aaaa-bbbb-cccc-111111111111": "system_admin"},
    )


def _base_claims(now: int) -> dict[str, Any]:
    # All synthetic tokens need `nbf` now that verify_entra_token requires it.
    return {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + 3600,
        "iat": now,
        "nbf": now,
        "oid": "u",
    }


def test_valid_token_decodes(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "oid": "user-1", "groups": []}
    token = _sign(rsa_keypair, claims_in)
    claims = verify_entra_token(token, _settings(), jwks=jwks)
    assert claims["oid"] == "user-1"


def test_expired_token_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "exp": now - 60, "iat": now - 3600}
    token = _sign(rsa_keypair, claims_in)
    with pytest.raises(EntraAuthError, match="expired"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_wrong_audience_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "aud": "api://wrong"}
    token = _sign(rsa_keypair, claims_in)
    with pytest.raises(EntraAuthError, match="audience"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_wrong_issuer_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "iss": "https://evil.example/v2.0"}
    token = _sign(rsa_keypair, claims_in)
    with pytest.raises(EntraAuthError, match="issuer"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_not_yet_valid_token_rejected(rsa_keypair, jwks) -> None:
    """A token whose `nbf` is in the future (beyond clock skew) is rejected."""
    now = int(time.time())
    claims_in = {**_base_claims(now), "nbf": now + 3600}
    token = _sign(rsa_keypair, claims_in)
    with pytest.raises(EntraAuthError, match="not yet valid"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_missing_nbf_rejected(rsa_keypair, jwks) -> None:
    """Tokens without an `nbf` claim are rejected (we require it explicitly)."""
    now = int(time.time())
    claims_in = {**_base_claims(now)}
    del claims_in["nbf"]
    token = _sign(rsa_keypair, claims_in)
    with pytest.raises(EntraAuthError, match="missing required claim"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_unknown_kid_rejected(rsa_keypair, jwks) -> None:
    pem = rsa_keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = int(time.time())
    token = jwt.encode(
        _base_claims(now),
        pem,
        algorithm="RS256",
        headers={"kid": "unknown-kid"},
    )
    with pytest.raises(EntraAuthError, match="no matching JWK"):
        verify_entra_token(token, _settings(), jwks=jwks)


def test_role_from_claims_system_admin_via_roles_claim() -> None:
    claims = {"oid": "u", "roles": ["system_admin"]}
    assert role_from_claims(claims, _settings()) == "system_admin"


def test_role_from_claims_via_group_map() -> None:
    claims = {"oid": "u", "groups": ["00000000-aaaa-bbbb-cccc-111111111111"]}
    assert role_from_claims(claims, _settings()) == "system_admin"


def test_role_from_claims_default_low_privilege() -> None:
    """Fallback is now the lowest-privilege role, not broker_admin —
    a misconfigured user can sign in but can't mutate data."""
    claims = {"oid": "u", "groups": []}
    assert role_from_claims(claims, _settings()) == "broker_viewer"


def test_role_from_claims_unknown_role_dropped() -> None:
    """Unrecognised role strings are not trusted verbatim."""
    claims = {"oid": "u", "roles": ["root", "superuser"]}
    assert role_from_claims(claims, _settings()) == "broker_viewer"
