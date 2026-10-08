"""Entra JWT validation — synthetic keys + tokens, no network.

Generates RSA keypairs for two directories, signs JWTs, builds the matching
JWKS, then runs them through `verify_entra_token` to confirm the signature,
audience, the directory's own issuer and tenant, and expiry are all checked.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import entra
from app.core.entra import EntraAuthError, verify_entra_token
from app.core.settings import Settings, clear_settings_cache, get_settings

KID = "test-kid-1"
OTHER_KID = "test-kid-2"
CLIENT_ID = "6f1c2a9e-3b4d-4c5e-8f60-718293a4b5c6"
AUDIENCE = CLIENT_ID
TENANT = "0b7e5a3c-1d2f-4e6a-9b8c-7d6e5f4a3b2c"
OTHER_TENANT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


def _issuer(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/v2.0"


@pytest.fixture(scope="module")
def rsa_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64u(n: int) -> str:
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _jwk(keypair, kid: str) -> dict[str, Any]:
    pub = keypair.public_key().public_numbers()
    return {"kty": "RSA", "use": "sig", "kid": kid, "alg": "RS256",
            "n": _b64u(pub.n), "e": _b64u(pub.e)}


@pytest.fixture(scope="module")
def jwks(rsa_keypair) -> dict[str, Any]:
    """The first directory's signing keys."""
    return {"keys": [_jwk(rsa_keypair, KID)]}


def _sign(keypair, claims: dict[str, Any], kid: str = KID) -> str:
    pem = keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


def _settings() -> Settings:
    return Settings(
        env="dev",
        auth_mode="entra",
        entra_tenant_id=TENANT,
        entra_client_id=CLIENT_ID,
        entra_audience=AUDIENCE,
    )


def _base_claims(now: int, tenant: str = TENANT) -> dict[str, Any]:
    # All synthetic tokens need `nbf` now that verify_entra_token requires it.
    return {
        "iss": _issuer(tenant),
        "aud": AUDIENCE,
        "exp": now + 3600,
        "iat": now,
        "nbf": now,
        "oid": "u",
        "tid": tenant,
        "scp": "access_as_user",
        "azp": CLIENT_ID,
    }


def _verify(token: str, jwks: dict[str, Any], tenant: str = TENANT) -> dict[str, Any]:
    return verify_entra_token(token, _settings(), tenant_id=tenant, jwks=jwks)


def test_valid_token_decodes(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "oid": "user-1", "groups": []}
    claims = _verify(_sign(rsa_keypair, claims_in), jwks)
    assert claims["oid"] == "user-1"
    assert claims["tid"] == TENANT


def test_expired_token_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "exp": now - 60, "iat": now - 3600}
    with pytest.raises(EntraAuthError, match="expired"):
        _verify(_sign(rsa_keypair, claims_in), jwks)


def test_wrong_audience_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "aud": "api://wrong"}
    with pytest.raises(EntraAuthError, match="audience"):
        _verify(_sign(rsa_keypair, claims_in), jwks)


def test_wrong_issuer_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    claims_in = {**_base_claims(now), "iss": "https://evil.example/v2.0"}
    with pytest.raises(EntraAuthError, match="issuer"):
        _verify(_sign(rsa_keypair, claims_in), jwks)


def test_not_yet_valid_token_rejected(rsa_keypair, jwks) -> None:
    """A token whose `nbf` is in the future (beyond clock skew) is rejected."""
    now = int(time.time())
    claims_in = {**_base_claims(now), "nbf": now + 3600}
    with pytest.raises(EntraAuthError, match="not yet valid"):
        _verify(_sign(rsa_keypair, claims_in), jwks)


def test_missing_nbf_rejected(rsa_keypair, jwks) -> None:
    """Tokens without an `nbf` claim are rejected (we require it explicitly)."""
    now = int(time.time())
    claims_in = {**_base_claims(now)}
    del claims_in["nbf"]
    with pytest.raises(EntraAuthError, match="missing required claim"):
        _verify(_sign(rsa_keypair, claims_in), jwks)


def test_unknown_kid_rejected(rsa_keypair, jwks) -> None:
    now = int(time.time())
    token = _sign(rsa_keypair, _base_claims(now), kid="unknown-kid")
    with pytest.raises(EntraAuthError, match="no matching JWK"):
        _verify(token, jwks)


@pytest.mark.parametrize("overrides", [
    {"scp": None}, {"scp": "other_permission"}, {"scp": ["access_as_user"]},
    {"tid": OTHER_TENANT}, {"oid": None}, {"idtyp": "app"}, {"azp": "other-client"},
])
def test_non_delegated_or_wrong_tenant_tokens_refused(rsa_keypair, jwks, overrides) -> None:
    token = _sign(rsa_keypair, {**_base_claims(int(time.time())), **overrides})
    with pytest.raises(EntraAuthError):
        _verify(token, jwks)


# ── One app registration, many directories ───────────────────────────────────
def test_token_from_another_directory_is_refused(rsa_keypair, other_keypair, jwks) -> None:
    """A genuine token from directory B, signed by B's keys, is no use on a firm
    whose directory is A: the firm's own key set does not verify it, and even a
    key set holding both keys leaves its issuer and tenant wrong."""
    now = int(time.time())
    other = _sign(other_keypair, _base_claims(now, OTHER_TENANT), kid=OTHER_KID)
    with pytest.raises(EntraAuthError, match="no matching JWK"):
        _verify(other, jwks)
    both = {"keys": [_jwk(rsa_keypair, KID), _jwk(other_keypair, OTHER_KID)]}
    with pytest.raises(EntraAuthError, match="issuer"):
        _verify(other, both)
    # The same token is accepted only by the firm whose directory issued it.
    other_jwks = {"keys": [_jwk(other_keypair, OTHER_KID)]}
    assert _verify(other, other_jwks, tenant=OTHER_TENANT)["tid"] == OTHER_TENANT


@pytest.mark.parametrize(("issuer_tenant", "tid"), [
    (OTHER_TENANT, TENANT),  # issued by another directory, claims ours
    (TENANT, OTHER_TENANT),  # our issuer, another directory's tenant claim
])
def test_issuer_and_tenant_must_both_name_the_firms_directory(
    rsa_keypair, jwks, issuer_tenant, tid,
) -> None:
    claims = {**_base_claims(int(time.time())), "iss": _issuer(issuer_tenant), "tid": tid}
    with pytest.raises(EntraAuthError, match=r"issuer|tenant"):
        _verify(_sign(rsa_keypair, claims), jwks)


@pytest.mark.parametrize("tenant", ["", "tenant-x", TENANT.upper(), "../common"])
def test_directory_must_be_a_canonical_guid(rsa_keypair, jwks, tenant) -> None:
    token = _sign(rsa_keypair, _base_claims(int(time.time())))
    with pytest.raises(EntraAuthError, match="GUID"):
        _verify(token, jwks, tenant=tenant)


def test_signing_keys_come_from_the_firms_directory(rsa_keypair, jwks, monkeypatch) -> None:
    """Without injected keys, the key set is fetched from the directory being
    validated against, never from a URL the token chose."""
    fetched: list[str] = []

    class _Key:
        def __init__(self, key: Any) -> None:
            self.key = key

    class _Client:
        def __init__(self, url: str) -> None:
            fetched.append(url)

        def get_signing_key_from_jwt(self, token: str) -> _Key:
            return _Key(rsa_keypair.public_key())

    monkeypatch.setattr(entra, "PyJWKClient", _Client)
    entra._jwks_client_cache.clear()
    token = _sign(rsa_keypair, _base_claims(int(time.time())))
    try:
        verify_entra_token(token, _settings(), tenant_id=TENANT)
        verify_entra_token(token, _settings(), tenant_id=TENANT)
    finally:
        entra._jwks_client_cache.clear()
    assert fetched == [f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"]


def test_start_up_refuses_a_custom_issuer(monkeypatch) -> None:
    """The legacy issuer and key URL variables may only repeat the platform
    directory's public-cloud values; anything else would be silently ignored."""
    monkeypatch.setenv("INSPRO_AUTH_MODE", "entra")
    monkeypatch.setenv("INSPRO_ENTRA_TENANT_ID", TENANT.upper())
    monkeypatch.setenv("INSPRO_ENTRA_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("INSPRO_ENTRA_ISSUER", _issuer(TENANT))
    clear_settings_cache()
    try:
        assert get_settings().entra_tenant_id == TENANT  # stored lowercase
        monkeypatch.setenv("INSPRO_ENTRA_ISSUER", "https://login.microsoftonline.us/x/v2.0")
        clear_settings_cache()
        with pytest.raises(RuntimeError, match="INSPRO_ENTRA_ISSUER"):
            get_settings()
    finally:
        monkeypatch.undo()
        clear_settings_cache()
