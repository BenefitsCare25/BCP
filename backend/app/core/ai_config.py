"""AI provider configuration — Google Vertex AI (Gemini), Singapore-resident.

Vertex/Gemini is the ONLY provider (AWS Bedrock and direct Anthropic were
removed). Three resolution sources, in priority order:

1. Per-tenant BYOK row in ``client_ai_configs`` (when ``db`` + ``client_id``
   are passed) — an optional per-company OVERRIDE, encrypted at rest and
   decrypted just-in-time.
2. The PLATFORM key on the ``platform_ai_settings`` singleton (when ``db`` is
   passed) — the global default every company runs on, set by a system admin
   in the UI. This is the normal way AI is configured.
3. Process-wide env vars: ``INSPRO_AI_PROVIDER=vertex`` (or just a present
   ``VERTEX_PROJECT``) + ``VERTEX_LOCATION`` / ``VERTEX_MODEL``; credentials via
   the standard Google ADC chain (``GOOGLE_APPLICATION_CREDENTIALS`` / workload
   identity).

Call ``load_ai_config(db, client_id)`` from a request handler so all three are
consulted; ``load_ai_config(db)`` (no client) skips only the BYOK layer.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

AISource = Literal["byok", "platform", "env", "none"]

# Vertex (Gemini) residency guard. Keep claims PII in Singapore: the "global"
# Vertex location routes to any geography, so it's refused in prod;
# asia-southeast1 is the Singapore region — and it is the ONLY one. Google has
# no second Singapore region (asia-southeast2 is Jakarta), so this set is
# complete, not a starting point.
DEFAULT_VERTEX_LOCATION = "asia-southeast1"
DEFAULT_VERTEX_MODEL = "gemini-3.5-flash"
DEFAULT_VERTEX_CAPACITY_MODE = "standard_paygo"
VERTEX_CAPACITY_MODES = frozenset({"standard_paygo", "provisioned_throughput"})
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
    capacity_mode: str = DEFAULT_VERTEX_CAPACITY_MODE


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


def assert_vertex_location_writable(location: str) -> str:
    """Reject a non-Singapore Vertex location at the WRITE boundary, in EVERY
    environment.

    ``assert_vertex_residency`` is the READ-path guard: it fail-closes in prod
    but only warns in dev/staging, because a local checkout may legitimately
    point env vars at another region and must not be bricked by it.

    STORING a credential is a different act. A saved row is promoted to prod
    unchanged, so a dev/staging save of ``us-central1`` is a latent prod outage
    the moment it ships — and a residency breach if it ever served a call. This
    is what makes the guarantee "Singapore only" rather than "Singapore only in
    prod"; keep the two functions separate.

    Raises ValueError (a validation failure), which the credential endpoints
    surface as a 400.

    RETURNS THE CANONICAL LOCATION, and callers must store what it returns, not
    their raw input. Vertex resource paths are case-sensitive
    (``projects/…/locations/<loc>/…``), so accepting ``"Asia-Southeast1"`` on
    the strength of a case-insensitive comparison and then persisting it
    verbatim stores a location every later call rejects.
    """
    loc = (location or "").strip().lower()
    if loc not in APPROVED_VERTEX_LOCATIONS:
        raise ValueError(
            f"Vertex location {location!r} is not permitted. Claim documents "
            "are Singapore-resident, so the only allowed region is "
            f"{DEFAULT_VERTEX_LOCATION}."
        )
    return loc


def _load_vertex_from_env() -> AIConfig | None:
    """Google Vertex AI (Gemini) — Singapore data-resident, per-token.

    Credentials come from the standard Google ADC chain (the google-genai client
    resolves ``GOOGLE_APPLICATION_CREDENTIALS`` / workload identity), so no key
    is stored here — ``api_key`` stays empty. ``VERTEX_PROJECT`` is required.
    """
    project = os.environ.get("VERTEX_PROJECT", "").strip()
    location = os.environ.get("VERTEX_LOCATION", "").strip() or DEFAULT_VERTEX_LOCATION
    model = os.environ.get("VERTEX_MODEL", "").strip() or DEFAULT_VERTEX_MODEL
    capacity_mode = (
        os.environ.get("VERTEX_CAPACITY_MODE", "").strip()
        or DEFAULT_VERTEX_CAPACITY_MODE
    )
    if not project:
        logger.error("INSPRO_AI_PROVIDER=vertex but VERTEX_PROJECT is unset.")
        return None
    if capacity_mode not in VERTEX_CAPACITY_MODES:
        logger.error("VERTEX_CAPACITY_MODE=%r is unsupported.", capacity_mode)
        return None
    if _prod_env() and os.environ.get("INSPRO_AI_CONFIG_VALIDATED", "").lower() != "true":
        logger.error(
            "Environment Vertex configuration is not activated; set "
            "INSPRO_AI_CONFIG_VALIDATED=true only after the structured-output probe."
        )
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
        capacity_mode=capacity_mode,
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


def _vertex_from_secret(
    location: str | None,
    model: str | None,
    secret_blob: str,
    source: AISource,
    label: str,
    capacity_mode: str | None = None,
) -> AIConfig | None:
    """Build a Vertex AIConfig from a decrypted stored secret.

    Shared by the BYOK row and the platform singleton — both store the location
    + Gemini model alongside a ``pack_vertex_secret`` blob ({project_id,
    service_account}). ``api_key`` carries the service-account JSON string; the
    adapter builds google-auth credentials from it. A malformed row falls
    through to the next source rather than 500-ing the AI surface; ``label``
    only identifies it in the log.
    """
    resolved_location = (location or "").strip() or DEFAULT_VERTEX_LOCATION
    resolved_model = (model or "").strip() or DEFAULT_VERTEX_MODEL
    resolved_capacity_mode = capacity_mode or DEFAULT_VERTEX_CAPACITY_MODE
    if resolved_capacity_mode not in VERTEX_CAPACITY_MODES:
        logger.warning("Vertex credentials for %s use invalid capacity mode", label)
        return None
    try:
        packed = json.loads(secret_blob)
        project_id = str(packed["project_id"])
        service_account_json = str(packed["service_account"])
    except (ValueError, KeyError, TypeError):
        logger.warning("Vertex credentials for %s are malformed", label)
        return None
    if not project_id:
        logger.warning("Vertex credentials for %s are missing project_id", label)
        return None
    try:
        assert_vertex_residency(resolved_location)
    except RuntimeError:
        # An out-of-region row is legal to save outside prod, so prod can
        # inherit one (a promoted DB, or INSPRO_ENV flipped after the fact).
        # Refusing it is right — but RAISING here would 500 /system/ai-status
        # and every AI path for EVERY company, since the platform key is
        # fleet-wide. Returning None keeps it fail-closed and degradable.
        logger.exception(
            "Vertex credentials for %s use non-resident location %r — refusing "
            "them and falling through to the next AI source",
            label,
            resolved_location,
        )
        return None
    return AIConfig(
        api_key=service_account_json,
        model=resolved_model,
        base_url=None,
        provider="vertex",
        gcp_project=project_id,
        gcp_location=resolved_location,
        source=source,
        capacity_mode=resolved_capacity_mode,
    )


def _decrypt_or_none(blob: bytes, label: str) -> str | None:
    """Decrypt a stored secret, logging (not raising) on expected failures.

    Expected failures only: missing/rotated master key (MasterKeyError), corrupt
    ciphertext (InvalidToken), non-UTF8 plaintext (UnicodeDecodeError ⊂
    ValueError). A programming error (AttributeError/TypeError) must NOT be
    swallowed as "fall through to the next source" — let it surface.
    """
    from cryptography.fernet import InvalidToken

    from app.core.crypto import MasterKeyError, decrypt_secret

    try:
        return decrypt_secret(blob)
    except (MasterKeyError, InvalidToken, ValueError) as exc:
        logger.exception(
            "Decrypt failed for %s — falling through to the next AI source: %s",
            label,
            exc,
        )
        return None


def _load_byok(db: Session, client_id: str) -> AIConfig | None:
    """Return the tenant's BYOK override, or ``None`` if no row / decrypt fails.

    Decrypt failures are logged but not raised — falling through to the
    platform key is safer than 500-ing the whole AI surface when one tenant's
    row is corrupt.
    """
    # Imported lazily so the auth-mode boot path doesn't need SQLAlchemy.
    from app.models.client_ai_config import ClientAIConfig

    row = db.query(ClientAIConfig).filter(ClientAIConfig.client_id == client_id).one_or_none()
    if row is None:
        return None
    if row.provider != "vertex":
        # Unrecognised / legacy provider (bedrock, anthropic, azure_foundry) —
        # don't guess; fall through rather than treat it as vertex.
        logger.warning(
            "BYOK row for client %s has unsupported provider %r — ignoring",
            client_id,
            row.provider,
        )
        return None
    if _prod_env() and not _row_is_activated(
        row, row.endpoint, row.model, row.capacity_mode
    ):
        logger.warning("BYOK row for client %s is saved but not activated", client_id)
        return None
    label = f"BYOK client {client_id}"
    secret = _decrypt_or_none(row.encrypted_api_key, label)
    if secret is None:
        return None
    return _vertex_from_secret(
        row.endpoint, row.model, secret, "byok", label, row.capacity_mode
    )


def _load_platform(db: Session) -> AIConfig | None:
    """Return the platform-wide key from the ``platform_ai_settings`` singleton.

    This is the DEFAULT every company runs on — a system admin sets it once in
    the UI. ``None`` when no key is stored (the row may exist carrying only
    limits), the provider is unsupported, or the secret won't decrypt.
    """
    from app.models.platform_ai_settings import SINGLETON_ID, PlatformAISetting

    row = db.get(PlatformAISetting, SINGLETON_ID)
    if row is None or not row.encrypted_service_account:
        return None
    if (row.provider or "vertex") != "vertex":
        logger.warning(
            "Platform AI settings have unsupported provider %r — ignoring",
            row.provider,
        )
        return None
    if _prod_env() and not _row_is_activated(
        row, row.location, row.model, row.capacity_mode
    ):
        logger.warning("Platform Vertex credentials are saved but not activated")
        return None
    label = "the platform AI key"
    secret = _decrypt_or_none(row.encrypted_service_account, label)
    if secret is None:
        return None
    return _vertex_from_secret(
        row.location, row.model, secret, "platform", label, row.capacity_mode
    )


class _ActivatedCredential(Protocol):
    @property
    def validation_status(self) -> str | None: ...

    @property
    def validated_fingerprint(self) -> str | None: ...

    @property
    def key_fingerprint(self) -> str | None: ...

    @property
    def validated_location(self) -> str | None: ...

    @property
    def validated_model(self) -> str | None: ...

    @property
    def validated_capacity_mode(self) -> str | None: ...


def _row_is_activated(
    row: _ActivatedCredential,
    location: str | None,
    model: str | None,
    capacity: str | None,
) -> bool:
    return bool(
        row.validation_status == "active"
        and row.validated_fingerprint == row.key_fingerprint
        and row.validated_location == (location or DEFAULT_VERTEX_LOCATION)
        and row.validated_model == (model or DEFAULT_VERTEX_MODEL)
        and row.validated_capacity_mode == (capacity or DEFAULT_VERTEX_CAPACITY_MODE)
    )


def load_ai_config(
    db: Session | None = None, client_id: str | None = None
) -> AIConfig | None:
    """Resolve AI provider configuration: BYOK → platform key → env.

    With ``db`` + ``client_id``, the tenant's BYOK override wins. With ``db``
    alone (background jobs without a tenant), the platform key is consulted.
    Without a session at all, only env can resolve.
    """
    if db is not None:
        if client_id:
            byok = _load_byok(db, client_id)
            if byok is not None:
                return byok
        platform = _load_platform(db)
        if platform is not None:
            return platform
    return _load_from_env()


