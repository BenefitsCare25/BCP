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
MailMode = Literal["log", "smtp", "acs"]
StorageMode = Literal["local", "azure"]


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
    # ── Retained document storage (claim receipts, dependant proofs) ──
    storage_mode: StorageMode = "local"
    storage_dir: str = ""
    storage_container: str = "documents"
    storage_account_url: str = ""
    storage_connection_string: str = ""


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


def _resolve_mail_mode(env: Env) -> MailMode:
    """Mail mode is fail-closed in production.

    The `log` mailer writes sign-in OTP codes to the application logs in
    cleartext — an account-takeover credential for anyone with log access.
    Fine for dev/staging; in prod a real delivery mode must be chosen
    explicitly, mirroring `_resolve_auth_mode`.
    """
    raw = os.environ.get("INSPRO_MAIL_MODE", "").strip().lower()
    if raw not in ("", "log", "smtp", "acs"):
        raise RuntimeError(
            f"INSPRO_MAIL_MODE={raw!r} is invalid — expected 'log', 'smtp' or 'acs'."
        )
    if env == "prod":
        if raw in ("", "log"):
            raise RuntimeError(
                "INSPRO_MAIL_MODE must be 'smtp' or 'acs' in production — the "
                "'log' mailer writes member sign-in codes to the logs in "
                "cleartext."
            )
        return raw  # type: ignore[return-value]
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
    )


def clear_settings_cache() -> None:
    """Tests that mutate env vars between cases call this to invalidate."""
    get_settings.cache_clear()
