"""Tenant-selector resolution across subdomain vs single-host (header) mode.

`resolve_host_info` is the one place that decides which tenant a request on the
HR / portal surfaces is for. The prod behaviour matters: on a deployment with no
custom domain there are no tenant subdomains to parse, so refusing the header in
prod would 400 every member and HR sign-in.

The fail-closed settings these surfaces depend on are pinned here too: the
environment name, explicit auth outside dev, and the public origins that
emailed links and CORS grants are built from.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers every table for the in-memory database
from app.core import tenant_resolution
from app.core.settings import (
    Settings,
    _resolve_env,
    assert_local_dev_database,
    clear_settings_cache,
    get_settings,
)
from app.core.tenancy_host import (
    SURFACE_HR,
    SURFACE_PORTAL,
    HostInfo,
    SlugError,
    resolve_host_info,
    resolve_tenant_context,
)
from app.core.tenant_resolution import (
    FirmContext,
    FirmOriginUnavailable,
    lookup_firm,
    public_origin,
)
from app.db.base import Base
from app.models import BrokerFirm, Client
from app.models.platform import TenantDomain
from app.services.client_slug import ALIAS_TAKEN, assign_slug


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
    monkeypatch.setenv("INSPRO_FRONTEND_ORIGIN", "https://portal.example.com")
    monkeypatch.setenv("INSPRO_CORS_ORIGINS", "https://portal.example.com")
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
    with pytest.raises(RuntimeError, match="INSPRO_TENANT_MODE"):
        get_settings()
    clear_settings_cache()


@pytest.mark.parametrize("configured_mode", ["", "log", "disabled", "smtp"])
def test_production_mail_fails_closed_without_smtp(prod_env, configured_mode):
    """A skipped SMTP setup must never leak invite credentials to logs."""
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

    assert get_settings().mail_mode == "disabled"
    mailer = get_mailer()
    assert isinstance(mailer, DisabledMailer)
    with pytest.raises(RuntimeError, match="Outbound mail is disabled"):
        mailer.send_member_invite(
            "member@example.com", "member@example.com", "One-Time-Secret-9",
            "https://example.com/portal/sign-in",
        )


# ── Fail-closed environment and auth mode ────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "dev"),
        ("", "dev"),
        ("dev", "dev"),
        ("development", "dev"),
        (" Staging ", "staging"),
        ("stg", "staging"),
        ("PROD", "prod"),
        ("production", "prod"),
    ],
)
def test_known_environment_names_resolve(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("INSPRO_ENV", raising=False)
    else:
        monkeypatch.setenv("INSPRO_ENV", raw)
    assert _resolve_env() == expected


@pytest.mark.parametrize("raw", ["prdo", "test", "qa", "local", "live"])
def test_unknown_environment_is_fatal(monkeypatch, raw):
    """An unknown name used to fall through to dev: mock sign-in, interactive
    docs and ephemeral secrets on whatever host carried the typo."""
    monkeypatch.setenv("INSPRO_ENV", raw)
    monkeypatch.setenv("INSPRO_AUTH_MODE", "mock")
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="INSPRO_ENV"):
        get_settings()
    clear_settings_cache()


@pytest.mark.parametrize("auth_mode", [None, "mock"])
def test_staging_refuses_implicit_or_mock_auth(monkeypatch, auth_mode):
    """Mock sign-in is development-only; staging must say `entra` explicitly."""
    monkeypatch.setenv("INSPRO_ENV", "staging")
    if auth_mode is None:
        monkeypatch.delenv("INSPRO_AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("INSPRO_AUTH_MODE", auth_mode)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="INSPRO_AUTH_MODE"):
        get_settings()
    clear_settings_cache()


def test_staging_accepts_entra_while_dev_defaults_to_mock(monkeypatch):
    monkeypatch.setenv("INSPRO_ENV", "staging")
    monkeypatch.setenv("INSPRO_AUTH_MODE", "entra")
    monkeypatch.setenv("INSPRO_ENTRA_TENANT_ID", "t")
    monkeypatch.setenv("INSPRO_ENTRA_CLIENT_ID", "c")
    clear_settings_cache()
    assert get_settings().auth_mode == "entra"

    monkeypatch.setenv("INSPRO_ENV", "dev")
    monkeypatch.delenv("INSPRO_AUTH_MODE", raising=False)
    clear_settings_cache()
    assert get_settings().auth_mode == "mock"
    clear_settings_cache()


# ── Production origins for emailed links and CORS ────────────────────────────


def test_production_origins_are_normalised(prod_env):
    prod_env.setenv("INSPRO_FRONTEND_ORIGIN", " https://Portal.Example.com/ ")
    prod_env.setenv(
        "INSPRO_CORS_ORIGINS", "https://portal.example.com, https://hr.example.com:8443/"
    )
    clear_settings_cache()
    settings = get_settings()
    assert settings.frontend_origin == "https://portal.example.com"
    assert settings.cors_origins == (
        "https://portal.example.com",
        "https://hr.example.com:8443",
    )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "http://portal.example.com",
        "portal.example.com",
        "https://portal.example.com/app",
        "https://portal.example.com?next=/portal",
        "https://user:secret@portal.example.com",
        "https://portal.example.com:abc",
        "https://*.example.com",
        "https://localhost:5173",
        "https://app.localhost",
        "https://127.0.0.1",
        "https://[::1]:8443",
        "https://0.0.0.0",
    ],
)
def test_production_refuses_unusable_frontend_origin(prod_env, origin):
    """Invite, sign-in and claim-update links are built from this origin; the
    old localhost default emailed members a link to their own machine."""
    if origin is None:
        prod_env.delenv("INSPRO_FRONTEND_ORIGIN", raising=False)
    else:
        prod_env.setenv("INSPRO_FRONTEND_ORIGIN", origin)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="INSPRO_FRONTEND_ORIGIN") as refused:
        get_settings()
    assert "secret" not in str(refused.value)


@pytest.mark.parametrize(
    "origins",
    [
        None,
        " , ",
        "*",
        "https://portal.example.com,*",
        "http://portal.example.com",
        "https://portal.example.com,http://localhost:5173",
        "https://localhost",
    ],
)
def test_production_refuses_unsafe_cors_origins(prod_env, origins):
    """Credentialed CORS with a wildcard echoes any caller's origin; unset falls
    back to the plaintext localhost development origins."""
    if origins is None:
        prod_env.delenv("INSPRO_CORS_ORIGINS", raising=False)
    else:
        prod_env.setenv("INSPRO_CORS_ORIGINS", origins)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="INSPRO_CORS_ORIGINS"):
        get_settings()


def test_development_keeps_local_origin_defaults(monkeypatch):
    monkeypatch.setenv("INSPRO_ENV", "dev")
    monkeypatch.delenv("INSPRO_FRONTEND_ORIGIN", raising=False)
    monkeypatch.delenv("INSPRO_CORS_ORIGINS", raising=False)
    clear_settings_cache()
    settings = get_settings()
    assert settings.frontend_origin == "http://localhost:5173"
    assert settings.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")
    clear_settings_cache()


# ── Developer seed/reset tooling ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ("prod", "sqlite:///inspro.db"),
        ("staging", "sqlite:///inspro.db"),
        ("dev", "postgresql+psycopg://admin:hunter2@db.example.com:5432/inspro"),
    ],
)
def test_dev_tooling_refuses_anything_but_local_sqlite(monkeypatch, env, url):
    monkeypatch.setenv("INSPRO_ENV", env)
    with pytest.raises(RuntimeError, match="INSPRO_ENV=dev") as refused:
        assert_local_dev_database(url, tool="scripts/example.py")
    assert "hunter2" not in str(refused.value)


def test_dev_tooling_runs_on_local_sqlite(monkeypatch):
    monkeypatch.delenv("INSPRO_ENV", raising=False)
    assert_local_dev_database("sqlite:///C:/inspro/backend/inspro.db", tool="t")
    monkeypatch.setenv("INSPRO_ENV", "dev")
    assert_local_dev_database("sqlite+pysqlite:///:memory:", tool="t")


# ── Neutral fallback domain ──────────────────────────────────────────────────


def test_fallback_domain_is_normalised(monkeypatch):
    monkeypatch.setenv("INSPRO_FIRM_FALLBACK_DOMAIN", " Brokers.Example.NET. ")
    clear_settings_cache()
    assert get_settings().firm_fallback_domain == "brokers.example.net"
    monkeypatch.delenv("INSPRO_FIRM_FALLBACK_DOMAIN")
    clear_settings_cache()
    assert get_settings().firm_fallback_domain == ""
    clear_settings_cache()


@pytest.mark.parametrize(
    "raw",
    [
        "https://brokers.example.net",
        "brokers",
        "*.example.net",
        "brokers.example.net/portal",
        "brokers.example.net:443",
        "bad_label.example.net",
        "-brokers.example.net",
        "10.0.0.1",
        # No room left for a 63-character firm label within 253.
        ".".join(["a" * 63, "b" * 63, "c" * 63, "net"]),
    ],
)
def test_fallback_domain_must_be_a_bare_domain_name(monkeypatch, raw):
    """Every firm is served on, and mails links to, `<firm-slug>.<this>`, so a
    value that cannot be the parent of a hostname stops the boot."""
    monkeypatch.setenv("INSPRO_FIRM_FALLBACK_DOMAIN", raw)
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="INSPRO_FIRM_FALLBACK_DOMAIN"):
        get_settings()
    clear_settings_cache()


def test_fallback_domain_refuses_a_local_name_in_production(prod_env):
    prod_env.setenv("INSPRO_FIRM_FALLBACK_DOMAIN", "brokers.localhost")
    clear_settings_cache()
    with pytest.raises(RuntimeError, match="local name"):
        get_settings()


# ── Host → firm → company ────────────────────────────────────────────────────


@pytest.fixture
def control_db() -> Iterator[Session]:
    """A private in-memory database: these tests create firms and domains freely."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def _firm(
    db: Session, slug: str, *, status: str = "active", owner: bool = False,
    domains: tuple[tuple[str, str, bool, str], ...] = (),
) -> BrokerFirm:
    """A firm plus its domains as (hostname, surface, is_primary, status)."""
    firm = BrokerFirm(name=slug.title(), slug=slug, status=status, is_platform_owner=owner)
    db.add(firm)
    db.flush()
    for hostname, surface, primary, domain_status in domains:
        db.add(TenantDomain(
            broker_firm_id=firm.id, hostname=hostname, surface=surface,
            is_primary=primary, status=domain_status,
        ))
    db.flush()
    return firm


def _settings(env: str, **overrides: object) -> Settings:
    return replace(
        get_settings(), env=env, platform_hosts=("platform.example.test",),
        firm_fallback_domain="brokers.example.test",
        frontend_origin="https://platform.example.test", **overrides,
    )


def _ctx(firm: BrokerFirm) -> FirmContext:
    return FirmContext(
        firm_id=firm.id, firm_slug=firm.slug, surface="all", is_platform_host=False,
        database_key="default", status=firm.status, host=f"{firm.slug}.example.test",
    )


def test_hosts_resolve_to_platform_domain_fallback_and_local_firms(control_db):
    owner = _firm(control_db, "owner", owner=True)
    brokera = _firm(control_db, "brokera", domains=(
        ("benefits.brokera.test", "client", True, "active"),
        ("pending.brokera.test", "all", False, "pending"),
    ))
    _firm(control_db, "brokerc", status="suspended")
    prod, dev = _settings("prod"), _settings("dev")

    platform = lookup_firm(control_db, "platform.example.test", prod)
    assert platform is not None and (platform.firm_id, platform.is_platform_host) == (
        owner.id, True,
    )
    domain = lookup_firm(control_db, "benefits.brokera.test", prod)
    assert domain is not None
    assert (domain.firm_id, domain.surface, domain.is_platform_host) == (
        brokera.id, "client", False,
    )
    assert lookup_firm(control_db, "pending.brokera.test", prod) is None
    # The neutral address works in production, for active firms only.
    fallback = lookup_firm(control_db, "brokera.brokers.example.test", prod)
    assert fallback is not None
    assert (fallback.firm_id, fallback.surface, fallback.is_platform_host) == (
        brokera.id, "all", False,
    )
    for env in (prod, dev):
        assert lookup_firm(control_db, "brokerc.brokers.example.test", env) is None
        # Never the owner's console on a broker-shaped name, even in dev.
        assert lookup_firm(control_db, "nobody.brokers.example.test", env) is None
        assert lookup_firm(control_db, "x.brokera.brokers.example.test", env) is None
    # `<firm-slug>.localhost` is a development convenience only.
    local = lookup_firm(control_db, "brokera.localhost", dev)
    assert local is not None and local.firm_id == brokera.id
    assert lookup_firm(control_db, "brokera.localhost", prod) is None
    assert lookup_firm(control_db, "elsewhere.example.test", prod) is None
    unmapped_dev = lookup_firm(control_db, "elsewhere.example.test", dev)
    assert unmapped_dev is not None and unmapped_dev.firm_id == owner.id


def test_company_alias_resolves_within_the_hosts_firm(control_db):
    """Aliases are unique per broker: the host picks the firm, then the alias."""
    brokera, brokerb = _firm(control_db, "brokera"), _firm(control_db, "brokerb")
    acme_a = Client(name="Acme A", broker_firm_id=brokera.id, slug="acme")
    acme_b = Client(name="Acme B", broker_firm_id=brokerb.id, slug="acme")
    only_b = Client(name="Only B", broker_firm_id=brokerb.id, slug="only-b")
    control_db.add_all([acme_a, acme_b, only_b])
    control_db.flush()
    acme = HostInfo(SURFACE_PORTAL, "acme")

    on_a = resolve_tenant_context(acme, control_db, _ctx(brokera))
    on_b = resolve_tenant_context(acme, control_db, _ctx(brokerb))
    assert on_a is not None and (on_a.client_id, on_a.broker_firm_id) == (acme_a.id, brokera.id)
    assert on_b is not None and (on_b.client_id, on_b.broker_firm_id) == (acme_b.id, brokerb.id)
    with pytest.raises(HTTPException) as foreign:
        resolve_tenant_context(HostInfo(SURFACE_HR, "only-b"), control_db, _ctx(brokera))
    assert (foreign.value.status_code, foreign.value.detail) == (404, "Unknown tenant.")
    # No host firm (local tools): a unique alias resolves, a shared one never
    # guesses between brokers.
    unbound = resolve_tenant_context(HostInfo(SURFACE_HR, "only-b"), control_db)
    assert unbound is not None and unbound.client_id == only_b.id
    with pytest.raises(HTTPException) as ambiguous:
        resolve_tenant_context(acme, control_db)
    assert ambiguous.value.status_code == 404


def test_company_aliases_are_unique_per_firm(control_db):
    brokera, brokerb = _firm(control_db, "brokera"), _firm(control_db, "brokerb")

    def company(firm: BrokerFirm, requested: str | None = None) -> Client:
        client = Client(name="Acme Pte Ltd", broker_firm_id=firm.id)
        control_db.add(client)
        control_db.flush()
        assign_slug(control_db, client, requested)
        control_db.flush()
        return client

    assert company(brokera).slug == "acme-pte-ltd"
    # The same alias in another firm is another company on another host.
    assert company(brokerb).slug == "acme-pte-ltd"
    assert company(brokera).slug == "acme-pte-ltd-2"
    with pytest.raises(SlugError) as clash:
        company(brokera, "acme-pte-ltd")
    # Says nothing about who else holds it.
    assert str(clash.value) == ALIAS_TAKEN
    assert company(brokerb, "acme-pte-ltd-2").slug == "acme-pte-ltd-2"


# ── Link origins ─────────────────────────────────────────────────────────────


def test_public_origin_prefers_the_firms_own_primary_address(control_db, monkeypatch):
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: _settings("prod"))
    firm = _firm(control_db, "brokera", domains=(
        ("staff.brokera.test", "staff", True, "active"),
        ("www.brokera.test", "all", True, "active"),
        ("benefits.brokera.test", "client", True, "active"),
        ("another.brokera.test", "client", False, "active"),
        ("old.brokera.test", "client", True, "disabled"),
    ))
    # Primary first; among primaries the surface's own domain beats an `all` one.
    assert public_origin(control_db, firm.id, "client") == "https://benefits.brokera.test"
    assert public_origin(control_db, firm.id, "staff") == "https://staff.brokera.test"

    for domain in control_db.query(TenantDomain).filter_by(broker_firm_id=firm.id):
        domain.is_primary = False
    control_db.flush()
    # No primary: any active domain serving it, the surface's own first.
    assert public_origin(control_db, firm.id, "client") == "https://another.brokera.test"


def test_public_origin_falls_back_then_refuses(control_db, monkeypatch):
    owner = _firm(control_db, "owner", owner=True)
    brokerb = _firm(control_db, "brokerb", domains=(
        ("staff.brokerb.test", "staff", True, "active"),
    ))
    settings = _settings("prod")
    monkeypatch.setattr(tenant_resolution, "get_settings", lambda: settings)
    # A staff-only domain is no address for a client link.
    assert public_origin(control_db, brokerb.id, "client") == "https://brokerb.brokers.example.test"
    assert public_origin(control_db, owner.id, "client") == "https://owner.brokers.example.test"

    settings = replace(settings, firm_fallback_domain="")
    assert public_origin(control_db, owner.id, "client") == "https://platform.example.test"
    with pytest.raises(FirmOriginUnavailable) as refused:
        public_origin(control_db, brokerb.id, "client")
    assert refused.value.firm_id == brokerb.id

    # Local development links `<firm-slug>.localhost` on the dev server instead.
    settings = replace(settings, env="dev", frontend_origin="http://localhost:5173")
    assert public_origin(control_db, brokerb.id, "client") == "http://brokerb.localhost:5173"
