"""AI provider configuration.

Two resolution sources in priority order:

1. Per-tenant BYOK row in ``client_ai_configs`` (when ``db`` + ``client_id``
   are passed) — encrypted at rest, decrypted just-in-time.
2. Process-wide env vars — original behaviour.

Azure AI Foundry exposes Claude via an Anthropic-compatible endpoint, so we
use the standard Anthropic SDK with a custom ``base_url``. To configure via
env, set:

    AZURE_FOUNDRY_ENDPOINT  e.g. https://<resource>.services.ai.azure.com/anthropic/
    AZURE_FOUNDRY_API_KEY   Azure access key
    AZURE_FOUNDRY_MODEL     defaults to "claude-sonnet-4-6"

If those are unset, the system falls back to direct Anthropic credentials
(ANTHROPIC_API_KEY + ANTHROPIC_MODEL) for local development.

For per-tenant BYOK, call ``load_ai_config(db, client_id)`` from a request
handler — the BYOK row (if any) takes precedence over env.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

DEFAULT_MODEL = "claude-sonnet-4-6"

logger = logging.getLogger(__name__)

AISource = Literal["byok", "env", "none"]
AZURE_FOUNDRY_HOST_SUFFIX = ".services.ai.azure.com"


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    model: str
    base_url: str | None
    provider: str  # "azure_foundry" | "anthropic"
    source: AISource = "env"


def normalize_foundry_endpoint(url: str) -> str:
    """Resolve to the Anthropic-compatible base URL.

    Handles two Azure AI Foundry URL formats:
    1. Resource-level:  https://<r>.services.ai.azure.com/anthropic/
    2. Project-level:   https://<r>.services.ai.azure.com/api/projects/<id>
       → auto-appends /anthropic/ so the Anthropic SDK reaches the right path.

    Also strips /v1/messages if the user pasted the full messages URL.
    """
    parsed = urlsplit(url.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or not hostname.endswith(AZURE_FOUNDRY_HOST_SUFFIX)
        or hostname == AZURE_FOUNDRY_HOST_SUFFIX.removeprefix(".")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "endpoint must be an HTTPS Azure AI Foundry URL under "
            "*.services.ai.azure.com"
        )
    path = parsed.path.replace("/v1/messages/", "/").replace("/v1/messages", "").rstrip("/")
    url = f"https://{hostname}{path}"
    # Project URL: .../api/projects/<id> — append /anthropic
    if "/api/projects/" in url and "/anthropic" not in url:
        url = url + "/anthropic"
    return url + "/"


def _load_from_env() -> AIConfig | None:
    foundry_endpoint = os.environ.get("AZURE_FOUNDRY_ENDPOINT", "").strip()
    foundry_key = os.environ.get("AZURE_FOUNDRY_API_KEY", "").strip()
    if foundry_endpoint and foundry_key:
        try:
            base_url = normalize_foundry_endpoint(foundry_endpoint)
        except ValueError:
            logger.error("AZURE_FOUNDRY_ENDPOINT is not an approved Azure Foundry URL")
            return None
        return AIConfig(
            api_key=foundry_key,
            model=os.environ.get("AZURE_FOUNDRY_MODEL", DEFAULT_MODEL).strip(),
            base_url=base_url,
            provider="azure_foundry",
            source="env",
        )
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if anthropic_key:
        return AIConfig(
            api_key=anthropic_key,
            model=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip(),
            base_url=None,
            provider="anthropic",
            source="env",
        )
    return None


def _load_byok(db: Session, client_id: str) -> AIConfig | None:
    """Return the tenant's BYOK config, or ``None`` if no row / decrypt fails.

    Decrypt failures are logged but not raised — falling through to the env
    fallback is safer than 500-ing the whole AI surface when one tenant's row
    is corrupt.
    """
    # Imported lazily so the auth-mode boot path doesn't need SQLAlchemy.
    from app.core.crypto import MasterKeyError, decrypt_secret
    from app.models.client_ai_config import ClientAIConfig

    row = db.query(ClientAIConfig).filter(ClientAIConfig.client_id == client_id).one_or_none()
    if row is None:
        return None
    try:
        api_key = decrypt_secret(row.encrypted_api_key)
    except (MasterKeyError, ValueError, Exception) as exc:
        logger.exception(
            "BYOK decrypt failed for client %s — falling back to env: %s",
            client_id,
            exc,
        )
        return None

    model = (row.model or DEFAULT_MODEL).strip()
    if row.provider == "azure_foundry":
        if not row.endpoint:
            logger.warning("BYOK row for client %s missing endpoint", client_id)
            return None
        try:
            base_url = normalize_foundry_endpoint(row.endpoint)
        except ValueError:
            logger.warning("BYOK row for client %s has an unapproved endpoint", client_id)
            return None
        return AIConfig(
            api_key=api_key,
            model=model,
            base_url=base_url,
            provider="azure_foundry",
            source="byok",
        )
    return AIConfig(
        api_key=api_key,
        model=model,
        base_url=None,
        provider="anthropic",
        source="byok",
    )


def load_ai_config(
    db: Session | None = None, client_id: str | None = None
) -> AIConfig | None:
    """Resolve AI provider configuration.

    With ``db`` + ``client_id``, looks up the tenant's BYOK row first; falls
    through to env on miss/decrypt-failure. Without them (legacy callers,
    background jobs), returns the env config or ``None``.
    """
    if db is not None and client_id:
        byok = _load_byok(db, client_id)
        if byok is not None:
            return byok
    return _load_from_env()


def is_configured() -> bool:
    """Env-only check — kept zero-arg for backward compatibility.

    Tenant-aware code should use ``load_ai_config(db, client_id) is not None``
    or ``resolve_ai_source`` instead.
    """
    return _load_from_env() is not None


def resolve_ai_source(
    db: Session | None = None, client_id: str | None = None
) -> AISource:
    """Which source backs ``load_ai_config(db, client_id)`` right now."""
    cfg = load_ai_config(db, client_id)
    return cfg.source if cfg else "none"
