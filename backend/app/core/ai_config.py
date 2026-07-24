"""AI provider configuration — Google Vertex AI (Gemini), Singapore-resident.

Vertex/Gemini is the ONLY provider (AWS Bedrock and direct Anthropic were
removed). Two resolution sources, in priority order:

1. Per-tenant BYOK row in ``client_ai_configs`` (when ``db`` + ``client_id``
   are passed) — the service-account JSON encrypted at rest, decrypted
   just-in-time.
2. Process-wide env vars: ``INSPRO_AI_PROVIDER=vertex`` (or just a present
   ``VERTEX_PROJECT``) + ``VERTEX_LOCATION`` / ``VERTEX_MODEL``; credentials via
   the standard Google ADC chain (``GOOGLE_APPLICATION_CREDENTIALS`` / workload
   identity).

For per-tenant BYOK, call ``load_ai_config(db, client_id)`` from a request
handler — the BYOK row (if any) takes precedence over env.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

AISource = Literal["byok", "env", "none"]

# Vertex (Gemini) residency guard. Keep claims PII in Singapore: the "global"
# Vertex location routes to any geography, so it's refused in prod;
# asia-southeast1 is the Singapore region.
DEFAULT_VERTEX_LOCATION = "asia-southeast1"
DEFAULT_VERTEX_MODEL = "gemini-2.5-flash"
APPROVED_VERTEX_LOCATIONS = frozenset({"asia-southeast1"})


@dataclass(frozen=True)
class AIConfig:
    # api_key carries the service-account JSON in BYOK mode; empty in env mode
    # (Google ADC supplies credentials). gcp_project + gcp_location pin the
    # Vertex project/region. base_url is unused (kept for a stable shape).
    api_key: str
    model: str
    base_url: str | None
    provider: str  # always "vertex"
    source: AISource = "env"
    gcp_project: str | None = None
    gcp_location: str | None = None


def _prod_env() -> bool:
    return os.environ.get("INSPRO_ENV", "dev").strip().lower() in ("prod", "production")


def assert_vertex_residency(location: str) -> None:
    """Fail-closed in prod on a Vertex location that could leave Singapore.

    The Vertex ``global`` location (and any non-approved region) can process
    outside Singapore — refused in prod, warned in dev/staging.
    """
    loc = (location or "").strip().lower()
    location_ok = loc in APPROVED_VERTEX_LOCATIONS
    if _prod_env():
        if not location_ok:
            raise RuntimeError(
                f"VERTEX_LOCATION={location!r} is not an approved residency "
                f"region {sorted(APPROVED_VERTEX_LOCATIONS)}. Gemini claims "
                "review must run in asia-southeast1 (Singapore)."
            )
        return
    if not location_ok:
        logger.warning(
            "VERTEX_LOCATION=%s is outside the approved residency region "
            "(asia-southeast1). Dev/staging only.",
            location,
        )


def _load_vertex_from_env() -> AIConfig | None:
    """Google Vertex AI (Gemini) — Singapore data-resident, per-token.

    Credentials come from the standard Google ADC chain (the google-genai client
    resolves ``GOOGLE_APPLICATION_CREDENTIALS`` / workload identity), so no key
    is stored here — ``api_key`` stays empty. ``VERTEX_PROJECT`` is required.
    """
    project = os.environ.get("VERTEX_PROJECT", "").strip()
    location = os.environ.get("VERTEX_LOCATION", "").strip() or DEFAULT_VERTEX_LOCATION
    model = os.environ.get("VERTEX_MODEL", "").strip() or DEFAULT_VERTEX_MODEL
    if not project:
        logger.error("INSPRO_AI_PROVIDER=vertex but VERTEX_PROJECT is unset.")
        return None
    assert_vertex_residency(location)
    return AIConfig(
        api_key="",
        model=model,
        base_url=None,
        provider="vertex",
        gcp_project=project,
        gcp_location=location,
        source="env",
    )


def _load_from_env() -> AIConfig | None:
    """Env-backed Vertex config. Resolves when the provider is explicitly
    ``vertex`` OR a ``VERTEX_PROJECT`` is present (so local dev needn't set the
    flag). Any other provider value is unsupported and yields None."""
    provider = os.environ.get("INSPRO_AI_PROVIDER", "").strip().lower()
    if provider == "vertex" or os.environ.get("VERTEX_PROJECT", "").strip():
        return _load_vertex_from_env()
    return None


def pack_vertex_secret(project_id: str, service_account_json: str) -> str:
    """Serialize the Vertex project + service-account JSON for encrypted storage.

    The service-account key is itself JSON; we wrap it with the target project
    id (which may differ from the SA's home project) so the BYOK row carries
    both. Stored in the row's encrypted secret.
    """
    return json.dumps(
        {"project_id": project_id, "service_account": service_account_json}
    )


def _byok_vertex(row: object, secret_blob: str, client_id: str) -> AIConfig | None:
    """Build a Vertex AIConfig from a decrypted BYOK row.

    ``row.endpoint`` holds the location, ``row.model`` the Gemini model id, and
    the decrypted ``secret_blob`` is the JSON packed by ``pack_vertex_secret``
    ({project_id, service_account}). ``api_key`` carries the service-account
    JSON string; the adapter builds google-auth credentials from it. Malformed
    rows fall through to env rather than 500-ing the AI surface.
    """
    location = (getattr(row, "endpoint", None) or "").strip() or DEFAULT_VERTEX_LOCATION
    model = (getattr(row, "model", None) or "").strip() or DEFAULT_VERTEX_MODEL
    try:
        packed = json.loads(secret_blob)
        project_id = str(packed["project_id"])
        service_account_json = str(packed["service_account"])
    except (ValueError, KeyError, TypeError):
        logger.warning("BYOK vertex row for client %s has malformed creds", client_id)
        return None
    if not project_id:
        logger.warning("BYOK vertex row for client %s missing project_id", client_id)
        return None
    assert_vertex_residency(location)
    return AIConfig(
        api_key=service_account_json,
        model=model,
        base_url=None,
        provider="vertex",
        gcp_project=project_id,
        gcp_location=location,
        source="byok",
    )


def _load_byok(db: Session, client_id: str) -> AIConfig | None:
    """Return the tenant's BYOK config, or ``None`` if no row / decrypt fails.

    Decrypt failures are logged but not raised — falling through to the env
    fallback is safer than 500-ing the whole AI surface when one tenant's row
    is corrupt.
    """
    # Imported lazily so the auth-mode boot path doesn't need SQLAlchemy.
    from cryptography.fernet import InvalidToken

    from app.core.crypto import MasterKeyError, decrypt_secret
    from app.models.client_ai_config import ClientAIConfig

    row = db.query(ClientAIConfig).filter(ClientAIConfig.client_id == client_id).one_or_none()
    if row is None:
        return None
    try:
        api_key = decrypt_secret(row.encrypted_api_key)
    # Expected decrypt failures only: missing/rotated master key (MasterKeyError),
    # corrupt ciphertext (InvalidToken), non-UTF8 plaintext (UnicodeDecodeError ⊂
    # ValueError). A programming error (AttributeError/TypeError) must NOT be
    # swallowed as "fall back to env" — let it surface.
    except (MasterKeyError, InvalidToken, ValueError) as exc:
        logger.exception(
            "BYOK decrypt failed for client %s — falling back to env: %s",
            client_id,
            exc,
        )
        return None

    if row.provider == "vertex":
        return _byok_vertex(row, api_key, client_id)
    # Unrecognised / legacy provider (bedrock, anthropic, azure_foundry) — don't
    # guess; fall through to env rather than treat it as vertex.
    logger.warning(
        "BYOK row for client %s has unsupported provider %r — ignoring",
        client_id,
        row.provider,
    )
    return None


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
