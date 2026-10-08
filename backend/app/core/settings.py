"""Environment-driven settings, loaded lazily.

Read via `get_settings()` so monkeypatching `os.environ` in tests works.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import SplitResult, urlsplit

logger = logging.getLogger(__name__)

AuthMode = Literal["mock", "entra"]
Env = Literal["dev", "staging", "prod"]
MailMode = Literal["disabled", "log", "smtp", "acs"]
StorageMode = Literal["local", "azure"]
TenantMode = Literal["subdomain", "header"]

# Every accepted INSPRO_ENV spelling. Anything else is fatal (_resolve_env).
_ENV_ALIASES: dict[str, Env] = {
    "dev": "dev",
    "development": "dev",
    "staging": "staging",
    "stg": "staging",
    "prod": "prod",
    "production": "prod",
}

# The local Vite dev server. Production must name its public origin instead.
_DEV_FRONTEND_ORIGIN = "http://localhost:5173"
_DEV_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


@dataclass(frozen=True)
class Settings:
    env: Env
    auth_mode: AuthMode
    # The platform directory (lowercase): the platform owner's firm signs in
    # with it until the firm names its own (`core/identity_providers.py`).
    # Each firm's directory is validated on its own (`core/entra.py`).
    entra_tenant_id: str
    # The one app registration every firm signs in through (multitenant).
    entra_client_id: str
    entra_audience: str
    # ── Employee portal (member auth + outbound mail) ──
    # Defaulted so tests can construct Settings(...) without portal fields;
    # get_settings() always resolves real values (fail-closed in prod).
    portal_jwt_secret: str = ""
    mail_mode: MailMode = "log"
    # The platform owner's public origin, a validated https origin in prod: the
    # default platform host, and where the owner firm's emailed links point
    # until it has a domain of its own (`tenant_resolution.public_origin`).
    frontend_origin: str = _DEV_FRONTEND_ORIGIN
    # Origins allowed credentialed cross-origin API calls. Production serves the
    # SPA from the API's own origin, so prod lists only https public origins.
    cors_origins: tuple[str, ...] = _DEV_CORS_ORIGINS
    # Apex domain for tenant-per-subdomain routing. `{slug}.portal.<base_domain>`
    # and `{slug}.hr.<base_domain>` resolve the tenant from the Host header.
    base_domain: str = "inspro.sg"
    # How the HR / portal surfaces learn which tenant a request is for.
    #   "subdomain" (default) — the Host header is the selector. Requires real
    #     per-tenant DNS + a wildcard cert.
    #   "header" — single-host deployments (no custom domain, e.g. the App
    #     Service default `*.azurewebsites.net`, where tenant subdomains cannot
    #     exist) let the SPA name the tenant via `X-Inspro-Tenant-Slug`.
    # The header only SELECTS a tenant, it never authorises one: authenticated
    # paths still require `token.cid == tenant.client_id`.
    tenant_mode: TenantMode = "subdomain"
    # ── Retained document storage (claim receipts, dependant proofs) ──
    storage_mode: StorageMode = "local"
    storage_dir: str = ""
    storage_container: str = "documents"
    storage_account_url: str = ""
    storage_connection_string: str = ""
    # ── Currency conversion (services/fx.py) ──
    # Foreign-currency claims are converted to the policy currency at the ECB
    # reference rate for the receipt date, fetched from Frankfurter (free, no
    # key, no account). Disabling it does NOT block foreign claims — they land
    # unconverted and flagged for a broker, which is the same path an outage
    # takes, so an air-gapped deploy degrades exactly like a bad network day.
    fx_enabled: bool = True
    fx_api_url: str = "https://api.frankfurter.dev/v1"
    fx_timeout_seconds: float = 3.0
    # RETRIES, not attempts: the budget is one call plus this many. Kept small
    # because the whole retry runs inside a member's submit.
    fx_max_retries: int = 2
    redis_url: str = ""
    require_document_scan: bool = False
    document_scan_command: str = ""
    document_scan_timeout_seconds: int = 30
    # ── White-label routing (core/tenant_resolution.py) ──
    # Hosts that serve the platform owner's firm and the master-admin console.
    # Every other host must be an active row in `tenant_domains`.
    platform_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    # Azure Front Door profile id. When set, every request must carry it in
    # X-Azure-FDID and the public host comes from X-Inspro-Edge-Host, a header
    # the Front Door rule set overwrites; the client can't choose it.
    front_door_id: str = ""
    # Neutral parent domain (no platform brand in it) for firms without a domain
    # of their own: `<firm-slug>.<this>` serves that firm and is the address its
    # emailed links use until it has one. Empty disables it.
    firm_fallback_domain: str = ""
    # ── Least-privilege database identity (db/roles.py, D13) ──
    # Whether creating a broker firm creates its Postgres schema in the same
    # request. Turn it off once the app connects as `inspro_app`, which has no
    # DDL rights: the firm is then left pending (its requests 503
    # `tenant_unavailable`) until the migration job provisions it.
    runtime_provisioning: bool = True


def _flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _positive_float(name: str, *, default: float, ceiling: float) -> float:
    """A tuning knob that must stay a sane positive number.

    Clamped rather than validated-and-refused: a typo'd timeout should not
    prevent the app booting, but neither should it be honoured — a `0` here
    would make every FX call fail instantly and quietly convert nothing.
    """
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("%s is not a number — using %s", name, default)
        return default
    if not value > 0:
        logger.warning("%s must be positive — using %s", name, default)
        return default
    return min(value, ceiling)


def _bounded_int(name: str, *, default: int, ceiling: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("%s is not an integer — using %s", name, default)
        return default
    return max(0, min(value, ceiling))


def _resolve_tenant_mode() -> TenantMode:
    """Tenant selector for the HR / portal surfaces. Typos are fatal, not silent —
    falling back to "subdomain" on a single-host deployment would 400 every
    member sign-in, and falling back to "header" would quietly drop the
    Host-header binding on a deployment that relies on it."""
    raw = os.environ.get("INSPRO_TENANT_MODE", "").strip().lower()
    if raw == "":
        return "subdomain"
    if raw not in ("subdomain", "header"):
        raise RuntimeError(
            f"INSPRO_TENANT_MODE={raw!r} is invalid — expected 'subdomain' or 'header'."
        )
    return raw  # type: ignore[return-value]


def _resolve_env() -> Env:
    """Deployment environment. Unset (or blank) means local development.

    Any other value must be a known spelling. Unknown values used to fall
    through to "dev" — mock sign-in, interactive docs, ephemeral secrets — so a
    typo such as `INSPRO_ENV=prdo` silently disarmed every production guard.
    """
    raw = os.environ.get("INSPRO_ENV", "").strip().lower()
    if not raw:
        return "dev"
    env = _ENV_ALIASES.get(raw)
    if env is None:
        raise RuntimeError(
            f"INSPRO_ENV={raw!r} is invalid — expected 'dev', 'staging' or 'prod'."
        )
    return env


def _resolve_auth_mode(env: Env) -> AuthMode:
    """Auth mode is fail-closed outside dev.

    - INSPRO_AUTH_MODE typo                        → refuse to start
    - INSPRO_AUTH_MODE missing + env=staging/prod  → refuse to start
    - INSPRO_AUTH_MODE=mock + env=staging/prod     → refuse to start (mock is dev-only)
    - INSPRO_AUTH_MODE missing + env=dev           → default to mock with WARNING
    """
    raw = os.environ.get("INSPRO_AUTH_MODE", "").strip().lower()

    if raw not in ("", "mock", "entra"):
        raise RuntimeError(
            f"INSPRO_AUTH_MODE={raw!r} is invalid — expected 'mock' or 'entra'."
        )

    if env != "dev":
        if raw == "":
            raise RuntimeError(
                f"INSPRO_AUTH_MODE must be set explicitly when INSPRO_ENV={env}. "
                "Set INSPRO_AUTH_MODE=entra."
            )
        if raw == "mock":
            raise RuntimeError(
                f"INSPRO_AUTH_MODE=mock is not allowed when INSPRO_ENV={env}; "
                "mock sign-in is development-only. Set INSPRO_AUTH_MODE=entra."
            )
        return "entra"

    if raw == "":
        logger.warning("INSPRO_AUTH_MODE not set — defaulting to 'mock' (dev only).")
        return "mock"
    return "entra" if raw == "entra" else "mock"


def _origin_parts(value: str) -> SplitResult | None:
    """`value` split as a bare https origin (`https://host[:port]`), else None."""
    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or port == 0
        or parts.username is not None
        or parts.path
        or parts.query
        or parts.fragment
    ):
        return None
    return parts


def _is_local_host(host: str) -> bool:
    host = host.rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _public_https_origin(name: str, raw: str) -> str:
    """Normalise `raw` to `https://host[:port]`, refusing anything else.

    Emailed links and credentialed CORS grants are built from these values, so
    a wildcard, plaintext, path-carrying or loopback value is a deployment
    error to stop at boot, not something to repair silently.
    """
    value = raw.strip().rstrip("/")
    parts = None if "*" in value else _origin_parts(value)
    if parts is None:
        # Never echo embedded credentials into the startup log.
        shown = value if "@" not in value else "***@" + value.rpartition("@")[2]
        raise RuntimeError(
            f"{name}: {shown!r} is not an https origin such as "
            "https://portal.example.com (no wildcard, path or credentials)."
        )
    if _is_local_host(parts.hostname or ""):
        raise RuntimeError(
            f"{name}: {value!r} is a local address; production needs the public origin."
        )
    return f"https://{parts.netloc.lower()}"


def _resolve_frontend_origin(env: Env) -> str:
    """The platform owner's public origin (platform host, owner firm's links).

    The localhost default suits development only: in prod it would email every
    member a link to their own machine, so prod must name its public origin.
    """
    if env != "prod":
        return (
            os.environ.get("INSPRO_FRONTEND_ORIGIN", _DEV_FRONTEND_ORIGIN)
            .strip()
            .rstrip("/")
        )
    raw = os.environ.get("INSPRO_FRONTEND_ORIGIN", "").strip()
    if not raw:
        raise RuntimeError(
            "INSPRO_FRONTEND_ORIGIN must be set in production to the public https "
            "origin used in emailed links, e.g. https://inspro-portal.azurewebsites.net."
        )
    return _public_https_origin("INSPRO_FRONTEND_ORIGIN", raw)


def _host_of(origin: str) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(origin).hostname or "").lower().rstrip(".")


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "testserver"})


def _resolve_platform_hosts(env: Env, frontend_origin: str) -> tuple[str, ...]:
    """Hosts that serve the platform owner's firm (and the master console).

    INSPRO_PLATFORM_HOSTS lists them explicitly. Unset, the public frontend
    origin's host is the platform host — so today's single-host production
    keeps serving the owner firm unchanged — plus the local names outside prod.
    """
    raw = os.environ.get("INSPRO_PLATFORM_HOSTS", "").strip()
    hosts = [h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip()]
    if not hosts:
        hosts = [_host_of(frontend_origin)]
    if env != "prod":
        hosts += ["localhost", "127.0.0.1", "testserver"]
    elif any(h in _LOCAL_HOSTS or h.endswith(".localhost") for h in hosts):
        raise RuntimeError("INSPRO_PLATFORM_HOSTS must not name a local host in production.")
    return tuple(dict.fromkeys(h for h in hosts if h))


_GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _resolve_front_door_id() -> str:
    raw = os.environ.get("INSPRO_FRONT_DOOR_ID", "").strip().lower()
    if raw and not _GUID.fullmatch(raw):
        raise RuntimeError("INSPRO_FRONT_DOOR_ID must be the Front Door profile's GUID.")
    return raw


_DNS_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
# Room left for the firm-slug label (up to 63 characters) and its dot.
_FALLBACK_DOMAIN_MAX = 253 - 64


def _resolve_firm_fallback_domain(env: Env) -> str:
    """Parent domain of every firm's neutral address, `<firm-slug>.<domain>`.

    Optional. Must be a bare DNS name of two or more labels — no scheme, port,
    path or wildcard — because each firm is served on, and emails links to, a
    hostname built from it. Anything else is a deployment error to stop at boot.
    """
    raw = os.environ.get("INSPRO_FIRM_FALLBACK_DOMAIN", "").strip().lower().rstrip(".")
    if not raw:
        return ""
    labels = raw.split(".")
    if (
        len(raw) > _FALLBACK_DOMAIN_MAX
        or len(labels) < 2
        or not all(_DNS_LABEL.fullmatch(label) for label in labels)
        or labels[-1].isdigit()  # an IP address, not a domain
    ):
        raise RuntimeError(
            f"INSPRO_FIRM_FALLBACK_DOMAIN={raw!r} is not a domain name such as "
            "brokers.example.net (no scheme, port, path or wildcard)."
        )
    if env == "prod" and _is_local_host(raw):
        raise RuntimeError("INSPRO_FIRM_FALLBACK_DOMAIN must not be a local name in production.")
    return raw


def _resolve_cors_origins(env: Env) -> tuple[str, ...]:
    """Origins allowed to call the API with credentials.

    A wildcard entry makes the CORS layer echo any caller's origin on a
    cookie-bearing request, and a plaintext or loopback entry extends that
    trust to whatever a network attacker or local process serves there — so
    prod accepts https public origins only. Unset falls back to the local Vite
    origins, so prod must set it explicitly.
    """
    raw = os.environ.get("INSPRO_CORS_ORIGINS")
    entries = [origin.strip() for origin in (raw or "").split(",") if origin.strip()]
    if env != "prod":
        return tuple(entries) if raw is not None else _DEV_CORS_ORIGINS
    if not entries:
        raise RuntimeError(
            "INSPRO_CORS_ORIGINS must be set in production to the public https "
            "origin(s), e.g. https://inspro-portal.azurewebsites.net."
        )
    return tuple(_public_https_origin("INSPRO_CORS_ORIGINS", entry) for entry in entries)


def assert_local_dev_database(database_url: str, *, tool: str) -> None:
    """Refuse developer seed/reset tooling outside a disposable local database.

    Those scripts insert demo identities or delete operational rows wholesale,
    so two signals must agree: the environment resolves to dev (unset counts as
    dev) AND the database is SQLite. Either alone is weak — a developer shell
    can carry a server INSPRO_DATABASE_URL, and INSPRO_ENV says nothing about
    where the engine points. Only the dialect is reported, never the URL, so a
    refusal cannot echo credentials.
    """
    env = _resolve_env()
    dialect = database_url.split(":", 1)[0].split("+", 1)[0].strip().lower()
    if env != "dev" or dialect != "sqlite":
        raise RuntimeError(
            f"{tool} only runs with INSPRO_ENV=dev against a local SQLite database "
            f"(resolved environment {env!r}, database dialect {dialect!r})."
        )


def _resolve_portal_jwt_secret(env: Env) -> str:
    """Portal member-token signing secret — fail-closed in prod.

    Missing in prod → refuse to start (a guessable/ephemeral secret would let
    anyone mint member tokens). Missing in dev/staging → ephemeral per-process
    secret with a WARNING (tokens die on restart, fine for local work).
    """
    raw = os.environ.get("INSPRO_PORTAL_JWT_SECRET", "").strip()
    if raw:
        if len(raw) < 32:
            raise RuntimeError(
                "INSPRO_PORTAL_JWT_SECRET must be at least 32 characters."
            )
        return raw
    if env == "prod":
        raise RuntimeError(
            "INSPRO_PORTAL_JWT_SECRET must be set in production for the "
            "employee portal. Generate one with: python -c "
            '"import secrets; print(secrets.token_urlsafe(48))"'
        )
    import secrets

    logger.warning(
        "INSPRO_PORTAL_JWT_SECRET not set — using an ephemeral secret "
        "(portal sessions won't survive a restart; dev/staging only)."
    )
    return secrets.token_urlsafe(48)


def _resolve_storage_mode(env: Env) -> StorageMode:
    raw = os.environ.get("INSPRO_STORAGE_MODE", "").strip().lower()
    if raw not in ("", "local", "azure"):
        raise RuntimeError(
            f"INSPRO_STORAGE_MODE={raw!r} is invalid — expected 'local' or 'azure'."
        )
    if raw == "":
        if env == "prod":
            logger.warning(
                "INSPRO_STORAGE_MODE not set in production — claim documents "
                "will be written to the container's LOCAL disk and lost on "
                "restart. Set INSPRO_STORAGE_MODE=azure."
            )
        return "local"
    return raw  # type: ignore[return-value]


def _resolve_redis_url(env: Env) -> str:
    raw = os.environ.get("INSPRO_REDIS_URL", "").strip()
    if not raw:
        if env == "prod":
            raise RuntimeError(
                "INSPRO_REDIS_URL must be set in production so rate limits and "
                "the claims AI cache are shared across workers and instances."
            )
        return ""
    if not raw.startswith(("redis://", "rediss://")):
        raise RuntimeError("INSPRO_REDIS_URL must use redis:// or rediss://")
    return raw


def _resolve_mail_mode(env: Env) -> MailMode:
    """Resolve mail delivery without exposing credentials in production.

    The `log` mailer writes invite one-time passwords to the application logs
    in cleartext — an account-takeover credential for anyone with log access.
    It remains useful in dev/staging. In prod, both an explicit `disabled` and
    the legacy `log` value resolve to a mailer that rejects delivery without
    logging the message. Treating legacy `log` this way keeps a rolling deploy
    safe while the older container is still serving.
    """
    raw = os.environ.get("INSPRO_MAIL_MODE", "").strip().lower()
    if raw == "acs":
        raise RuntimeError(
            "INSPRO_MAIL_MODE=acs is not implemented. Configure a verified SMTP sender."
        )
    if raw not in ("", "disabled", "log", "smtp", "acs"):
        raise RuntimeError(
            f"INSPRO_MAIL_MODE={raw!r} is invalid — expected 'disabled', "
            "'log', 'smtp' or 'acs'."
        )
    if env == "prod":
        if raw in ("", "disabled", "log"):
            return "disabled"
        host = os.environ.get("INSPRO_SMTP_HOST", "").strip()
        user = os.environ.get("INSPRO_SMTP_USER", "").strip()
        password = os.environ.get("INSPRO_SMTP_PASSWORD", "")
        sender = os.environ.get("INSPRO_SMTP_FROM", user).strip()
        if raw == "smtp" and not any((host, user, password, sender)):
            return "disabled"
        if not host or not sender:
            raise RuntimeError(
                "Production SMTP requires INSPRO_SMTP_HOST and "
                "INSPRO_SMTP_FROM (or INSPRO_SMTP_USER)."
            )
        if "@" not in sender:
            raise RuntimeError("INSPRO_SMTP_FROM must be a valid email address.")
        if user and not password:
            raise RuntimeError(
                "INSPRO_SMTP_PASSWORD is required when INSPRO_SMTP_USER is set."
            )
        try:
            port = int(os.environ.get("INSPRO_SMTP_PORT", "587"))
        except ValueError as exc:
            raise RuntimeError("INSPRO_SMTP_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("INSPRO_SMTP_PORT must be between 1 and 65535.")
        return "smtp"
    if raw == "":
        return "log"
    return raw  # type: ignore[return-value]


def _refuse_custom_entra_endpoints(tenant_id: str) -> None:
    """Fail start-up when a legacy issuer or key URL names another authority.

    Tokens are validated against each firm's own directory at
    login.microsoftonline.com (`core/entra.py`), so `INSPRO_ENTRA_ISSUER` and
    `INSPRO_ENTRA_JWKS_URL` no longer choose anything. A deployment that still
    sets them to the platform directory's public-cloud values keeps working; any
    other value would be silently ignored, so it is refused instead.
    """
    expected = {
        "INSPRO_ENTRA_ISSUER": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        "INSPRO_ENTRA_JWKS_URL": (
            f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        ),
    }
    for name, value in expected.items():
        raw = os.environ.get(name, "").strip().rstrip("/").lower()
        if raw and raw != value:
            raise RuntimeError(
                f"{name} is no longer configurable: tokens are validated against each "
                "broker firm's own directory at login.microsoftonline.com. Remove it."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = _resolve_env()
    auth_mode = _resolve_auth_mode(env)

    # Boot-time: refuse to start if the BYOK master key is missing/malformed
    # so a misconfigured deploy fails loudly instead of crashing on first
    # /ai-config request. Imported lazily to keep this module dep-free.
    from app.core.crypto import validate_master_key

    validate_master_key()

    tenant_id = os.environ.get("INSPRO_ENTRA_TENANT_ID", "").strip().lower()
    client_id = os.environ.get("INSPRO_ENTRA_CLIENT_ID", "").strip()
    audience = os.environ.get("INSPRO_ENTRA_AUDIENCE", client_id).strip()

    if auth_mode == "entra":
        # Don't allow silent disabling of the audience check — that's how
        # attackers replay tokens issued for a different app.
        missing = [
            name
            for name, val in (
                ("INSPRO_ENTRA_TENANT_ID", tenant_id),
                ("INSPRO_ENTRA_CLIENT_ID", client_id),
                ("INSPRO_ENTRA_AUDIENCE", audience),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "INSPRO_AUTH_MODE=entra but missing env vars: "
                + ", ".join(missing)
            )
        _refuse_custom_entra_endpoints(tenant_id)

    frontend_origin = _resolve_frontend_origin(env)
    return Settings(
        env=env,
        auth_mode=auth_mode,
        entra_tenant_id=tenant_id,
        entra_client_id=client_id,
        entra_audience=audience,
        portal_jwt_secret=_resolve_portal_jwt_secret(env),
        mail_mode=_resolve_mail_mode(env),
        frontend_origin=frontend_origin,
        platform_hosts=_resolve_platform_hosts(env, frontend_origin),
        front_door_id=_resolve_front_door_id(),
        firm_fallback_domain=_resolve_firm_fallback_domain(env),
        runtime_provisioning=_flag("INSPRO_RUNTIME_PROVISIONING", default=True),
        cors_origins=_resolve_cors_origins(env),
        base_domain=os.environ.get("INSPRO_BASE_DOMAIN", "inspro.sg")
        .strip()
        .lower()
        .strip(".")
        or "inspro.sg",
        tenant_mode=_resolve_tenant_mode(),
        storage_mode=_resolve_storage_mode(env),
        storage_dir=os.environ.get("INSPRO_STORAGE_DIR", "").strip(),
        storage_container=os.environ.get(
            "INSPRO_STORAGE_CONTAINER", "documents"
        ).strip(),
        storage_account_url=os.environ.get(
            "INSPRO_STORAGE_ACCOUNT_URL", ""
        ).strip().rstrip("/"),
        storage_connection_string=os.environ.get(
            "INSPRO_STORAGE_CONNECTION_STRING", ""
        ).strip(),
        fx_enabled=_flag("INSPRO_FX_ENABLED", default=True),
        fx_api_url=os.environ.get(
            "INSPRO_FX_API_URL", "https://api.frankfurter.dev/v1"
        ).strip().rstrip("/")
        or "https://api.frankfurter.dev/v1",
        fx_timeout_seconds=_positive_float(
            "INSPRO_FX_TIMEOUT_SECONDS", default=3.0, ceiling=30.0
        ),
        fx_max_retries=_bounded_int("INSPRO_FX_MAX_RETRIES", default=2, ceiling=5),
        redis_url=_resolve_redis_url(env),
        require_document_scan=_flag(
            "INSPRO_REQUIRE_DOCUMENT_SCAN", default=env == "prod"
        ),
        document_scan_command=os.environ.get(
            "INSPRO_DOCUMENT_SCAN_COMMAND", "clamscan" if env == "prod" else ""
        ).strip(),
        document_scan_timeout_seconds=max(
            1,
            _bounded_int(
                "INSPRO_DOCUMENT_SCAN_TIMEOUT_SECONDS", default=30, ceiling=120
            ),
        ),
    )


def clear_settings_cache() -> None:
    """Tests that mutate env vars between cases call this to invalidate."""
    get_settings.cache_clear()
