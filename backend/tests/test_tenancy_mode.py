"""Tenant-selector resolution across subdomain vs single-host (header) mode.

`resolve_host_info` is the one place that decides which tenant a request on the
HR / portal surfaces is for. The prod behaviour matters: on a deployment with no
custom domain there are no tenant subdomains to parse, so refusing the header in
prod would 400 every member and HR sign-in.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.settings import clear_settings_cache
from app.core.tenancy_host import (
    SURFACE_HR,
    SURFACE_PORTAL,
    HostInfo,
    resolve_host_info,
)


def _request(host_info: HostInfo | None = None) -> SimpleNamespace:
    """Stand-in for a Starlette Request — `resolve_host_info` only reads state."""
    return SimpleNamespace(state=SimpleNamespace(host_info=host_info))


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch):
    """Minimum env for get_settings() to resolve a prod Settings."""
    monkeypatch.setenv("INSPRO_ENV", "prod")
    monkeypatch.setenv("INSPRO_AUTH_MODE", "entra")
    monkeypatch.setenv("INSPRO_ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("INSPRO_ENTRA_CLIENT_ID", "c")
    monkeypatch.setenv("INSPRO_MAIL_MODE", "smtp")
    monkeypatch.setenv("INSPRO_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("INSPRO_SMTP_USER", "mailer@example.com")
    monkeypatch.setenv("INSPRO_SMTP_FROM", "mailer@example.com")
    monkeypatch.setenv("INSPRO_SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("INSPRO_PORTAL_JWT_SECRET", "x" * 48)
    monkeypatch.setenv("INSPRO_STORAGE_MODE", "azure")
    monkeypatch.setenv("INSPRO_REDIS_URL", "rediss://cache.example:10000/0")
    clear_settings_cache()
    yield monkeypatch
    clear_settings_cache()


def test_header_rejected_in_prod_under_subdomain_mode(prod_env):
    """Default mode: prod trusts only the Host header, never a client-supplied slug."""
    prod_env.delenv("INSPRO_TENANT_MODE", raising=False)
    clear_settings_cache()
    assert resolve_host_info(_request(), SURFACE_HR, "acme") is None


def test_header_accepted_in_prod_under_header_mode(prod_env):
    """Single-host deployment: the SPA names the tenant, and prod must honour it."""
    prod_env.setenv("INSPRO_TENANT_MODE", "header")
    clear_settings_cache()
    assert resolve_host_info(_request(), SURFACE_HR, "acme") == HostInfo(SURFACE_HR, "acme")
    assert resolve_host_info(_request(), SURFACE_PORTAL, "acme") == HostInfo(SURFACE_PORTAL, "acme")


def test_real_host_always_wins_over_header(prod_env):
    """A parsed subdomain is authoritative — a header can't redirect it elsewhere."""
    prod_env.setenv("INSPRO_TENANT_MODE", "header")
    clear_settings_cache()
    real = HostInfo(SURFACE_PORTAL, "acme")
    assert resolve_host_info(_request(real), SURFACE_PORTAL, "beta") == real


def test_malformed_slug_header_is_ignored(prod_env):
    """Slug must still be a valid DNS label — no path/host injection via the header."""
    prod_env.setenv("INSPRO_TENANT_MODE", "header")
    clear_settings_cache()
    for bad in ("", "  ", "-acme", "acme-", "ac--me", "acme.beta", "a/b", "A" * 64):
        assert resolve_host_info(_request(), SURFACE_HR, bad) is None


def test_header_accepted_in_non_prod_regardless_of_mode(monkeypatch):
    """Preserves the pre-existing localhost testability behaviour."""
    monkeypatch.setenv("INSPRO_ENV", "dev")
    monkeypatch.delenv("INSPRO_TENANT_MODE", raising=False)
    clear_settings_cache()
    assert resolve_host_info(_request(), SURFACE_HR, "acme") == HostInfo(SURFACE_HR, "acme")
    clear_settings_cache()


def test_invalid_tenant_mode_is_fatal(monkeypatch):
    """A typo must not silently pick a mode — either default would break a surface."""
    monkeypatch.setenv("INSPRO_ENV", "dev")
    monkeypatch.setenv("INSPRO_TENANT_MODE", "subdomains")
    clear_settings_cache()
    from app.core.settings import get_settings

    with pytest.raises(RuntimeError, match="INSPRO_TENANT_MODE"):
        get_settings()
    clear_settings_cache()


@pytest.mark.parametrize("configured_mode", ["", "log", "disabled", "smtp"])
def test_production_mail_fails_closed_without_smtp(prod_env, configured_mode):
    """A skipped SMTP setup must never leak OTP or invite credentials to logs."""
    if configured_mode:
        prod_env.setenv("INSPRO_MAIL_MODE", configured_mode)
    else:
        prod_env.delenv("INSPRO_MAIL_MODE", raising=False)
    for name in (
        "INSPRO_SMTP_HOST",
        "INSPRO_SMTP_USER",
        "INSPRO_SMTP_FROM",
        "INSPRO_SMTP_PASSWORD",
    ):
        prod_env.delenv(name, raising=False)
    clear_settings_cache()

    from app.core.mailer import DisabledMailer, get_mailer
    from app.core.settings import get_settings

    assert get_settings().mail_mode == "disabled"
    mailer = get_mailer()
    assert isinstance(mailer, DisabledMailer)
    with pytest.raises(RuntimeError, match="Outbound mail is disabled"):
        mailer.send_otp("member@example.com", "123456", "https://example.com/secret")
