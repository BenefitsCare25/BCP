"""Environment-driven settings, loaded lazily.

Read via `get_settings()` so monkeypatching `os.environ` in tests works.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

logger = logging.getLogger(__name__)

AuthMode = Literal["mock", "entra"]
Env = Literal["dev", "staging", "prod"]
MailMode = Literal["disabled", "log", "smtp", "acs"]
StorageMode = Literal["local", "azure"]
TenantMode = Literal["subdomain", "header"]


@dataclass(frozen=True)
class Settings:
    env: Env
    auth_mode: AuthMode
    entra_tenant_id: str
    entra_client_id: str
    entra_audience: str
    entra_issuer: str
    entra_jwks_url: str
    entra_group_role_map: dict[str, str]
    # ── Employee portal (member OTP auth) ──
    # Defaulted so tests can construct Settings(...) without portal fields;
    # get_settings() always resolves real values (fail-closed in prod).
    portal_jwt_secret: str = ""
    portal_token_ttl_hours: int = 12
    mail_mode: MailMode = "log"
    frontend_origin: str = "http://localhost:5173"
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


def _split_role_map(raw: str) -> dict[str, str]:
    """Parse `<group_id>:<role>,<group_id>:<role>`."""
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if ":" not in pair:
            continue
        gid, role = pair.split(":", 1)
        gid, role = gid.strip(), role.strip()
        if gid and role:
            out[gid] = role
    return out


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
    raw = os.environ.get("INSPRO_ENV", "dev").strip().lower()
    if raw in ("prod", "production"):
        return "prod"
    if raw in ("staging", "stg"):
        return "staging"
    return "dev"


def _resolve_auth_mode(env: Env) -> AuthMode:
    """Auth mode is fail-closed in non-dev environments.

    - INSPRO_AUTH_MODE=mock + env=prod         → refuse to start (mock is dev-only)
    - INSPRO_AUTH_MODE typo + env=prod         → refuse to start
    - INSPRO_AUTH_MODE missing + env=prod      → refuse to start
    - INSPRO_AUTH_MODE missing + env=dev/staging → default to mock with WARNING
    """
    raw = os.environ.get("INSPRO_AUTH_MODE", "").strip().lower()

    if raw not in ("", "mock", "entra"):
        raise RuntimeError(
            f"INSPRO_AUTH_MODE={raw!r} is invalid — expected 'mock' or 'entra'."
        )

    if env == "prod":
        if raw == "":
            raise RuntimeError(
                "INSPRO_AUTH_MODE must be set explicitly in production. "
                "Set INSPRO_AUTH_MODE=entra."
            )
        if raw == "mock":
            raise RuntimeError(
                "INSPRO_AUTH_MODE=mock is not allowed in production. "
                "Set INSPRO_AUTH_MODE=entra."
            )
        return "entra"

    if raw == "":
        logger.warning(
            "INSPRO_AUTH_MODE not set — defaulting to 'mock' (dev/staging only)."
        )
        return "mock"
    return "entra" if raw == "entra" else "mock"


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

    The `log` mailer writes sign-in OTP codes to the application logs in
    cleartext — an account-takeover credential for anyone with log access.
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = _resolve_env()
    auth_mode = _resolve_auth_mode(env)

    # Boot-time: refuse to start if the BYOK master key is missing/malformed
    # so a misconfigured deploy fails loudly instead of crashing on first
    # /ai-config request. Imported lazily to keep this module dep-free.
    from app.core.crypto import validate_master_key

    validate_master_key()

    tenant_id = os.environ.get("INSPRO_ENTRA_TENANT_ID", "").strip()
    client_id = os.environ.get("INSPRO_ENTRA_CLIENT_ID", "").strip()
    audience = os.environ.get("INSPRO_ENTRA_AUDIENCE", client_id).strip()
    issuer = os.environ.get(
        "INSPRO_ENTRA_ISSUER",
        f"https://login.microsoftonline.com/{tenant_id}/v2.0" if tenant_id else "",
    ).strip()
    jwks_url = os.environ.get(
        "INSPRO_ENTRA_JWKS_URL",
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys" if tenant_id else "",
    ).strip()

    if auth_mode == "entra":
        # Don't allow silent disabling of the audience check — that's how
        # attackers replay tokens issued for a different app.
        missing = [
            name
            for name, val in (
                ("INSPRO_ENTRA_TENANT_ID", tenant_id),
                ("INSPRO_ENTRA_CLIENT_ID", client_id),
                ("INSPRO_ENTRA_AUDIENCE", audience),
                ("INSPRO_ENTRA_ISSUER", issuer),
                ("INSPRO_ENTRA_JWKS_URL", jwks_url),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                "INSPRO_AUTH_MODE=entra but missing env vars: "
                + ", ".join(missing)
            )

    return Settings(
        env=env,
        auth_mode=auth_mode,
        entra_tenant_id=tenant_id,
        entra_client_id=client_id,
        entra_audience=audience,
        entra_issuer=issuer,
        entra_jwks_url=jwks_url,
        entra_group_role_map=_split_role_map(
            os.environ.get("INSPRO_ENTRA_GROUP_ROLE_MAP", "")
        ),
        portal_jwt_secret=_resolve_portal_jwt_secret(env),
        portal_token_ttl_hours=int(
            os.environ.get("INSPRO_PORTAL_TOKEN_TTL_HOURS", "12")
        ),
        mail_mode=_resolve_mail_mode(env),
        frontend_origin=os.environ.get(
            "INSPRO_FRONTEND_ORIGIN", "http://localhost:5173"
        ).strip().rstrip("/"),
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
