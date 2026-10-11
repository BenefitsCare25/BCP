"""Minimal Gemini reachability probe, shared by both credential surfaces.

Per-company BYOK (`api/v1/ai_config.py`) and the platform key
(`api/v1/platform_ai_settings.py`) must validate a service-account the SAME
way, so the "ping Vertex and translate the failure" logic lives here once.
Callers own persistence: the spend log, `last_validated_at`, and the audit
entry differ per surface.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import NamedTuple

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
    RateLimitError,
)

from app.core.ai_config import (
    DEFAULT_VERTEX_CAPACITY_MODE,
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    AIConfig,
    assert_vertex_residency,
)
from app.models import ClientAIConfig, PlatformAISetting

logger = logging.getLogger(__name__)

_TEST_PROMPT = (
    "Call emit_claim_review with empty arrays, summary='probe', and confidence=1."
)
# Vertex's first call builds google-auth credentials + a fresh HTTP client, so
# it needs headroom beyond a bare HTTP call.
VERTEX_TEST_TIMEOUT_SECONDS = 20.0
_TEST_MAX_TOKENS = 256


def project_id_from_service_account(service_account_json: str) -> str | None:
    """Read ``project_id`` out of a service-account JSON key, or None if absent."""
    try:
        project_id = str(json.loads(service_account_json)["project_id"])
    except (ValueError, KeyError, TypeError):
        return None
    return project_id or None


class VertexProbeResult(NamedTuple):
    error: str | None
    latency_ms: int
    model: str
    # The failure says nothing about the credential or its configuration (a
    # timeout, an unreachable endpoint, a 429 or a 5xx): a stored key that is
    # active stays active rather than being switched off by a diagnostic click.
    transient: bool = False


def probe_vertex(
    *,
    location: str | None,
    model: str | None,
    service_account_json: str | None,
    project_id: str | None,
    source: str,
    capacity_mode: str | None = None,
) -> VertexProbeResult:
    """Make a minimal structured Gemini call with the saved capacity mode.

    ``error`` is None on success. Never raises: every provider failure is
    translated to a short operator-readable string, because this runs behind a
    "Test connection" button and a stack trace would be useless there. The
    probe sends the same capacity-pool header live calls will, so a validated
    mode is one that was actually exercised.
    """
    resolved_location = (location or DEFAULT_VERTEX_LOCATION).strip() or DEFAULT_VERTEX_LOCATION
    resolved_model = (model or DEFAULT_VERTEX_MODEL).strip() or DEFAULT_VERTEX_MODEL

    if not project_id or not service_account_json:
        return VertexProbeResult(
            "Enter the GCP location and service-account JSON key to test.",
            0,
            resolved_model,
        )
    try:
        assert_vertex_residency(resolved_location)
    except RuntimeError as exc:
        return VertexProbeResult(str(exc), 0, resolved_model)

    from app.services.vertex_gemini import build_gemini_client

    cfg = AIConfig(
        api_key=service_account_json,
        model=resolved_model,
        base_url=None,
        provider="vertex",
        gcp_project=project_id,
        gcp_location=resolved_location,
        source=source,  # type: ignore[arg-type]
        capacity_mode=capacity_mode or DEFAULT_VERTEX_CAPACITY_MODE,
    )

    started = time.perf_counter()
    error: str | None = None
    transient = False
    try:
        # Validate the production claim-review contract, not a toy boolean.
        # A provider can support a one-field function while rejecting or
        # truncating the nested arrays the worker actually requires.
        from app.services.claim_ai import CLAIM_REVIEW_TOOL_SCHEMA

        client = build_gemini_client(cfg, timeout=VERTEX_TEST_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=resolved_model,
            max_tokens=_TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
            tools=[CLAIM_REVIEW_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": "emit_claim_review"},
            thinking_level="MINIMAL",
        )
        block = next(
            (part for part in response.content if getattr(part, "type", None) == "tool_use"),
            None,
        )
        payload = getattr(block, "input", {}) if block is not None else {}
        if response.stop_reason == "max_tokens":
            error = "Vertex structured-output probe was truncated."
        elif block is None or getattr(block, "name", None) != "emit_claim_review":
            error = "Vertex responded but did not support the required structured output."
        elif not (
            isinstance(payload, dict)
            and isinstance(payload.get("field_comparisons"), list)
            and isinstance(payload.get("rule_results"), list)
            and isinstance(payload.get("required_documents_check"), list)
            and isinstance(payload.get("summary"), str)
            and isinstance(payload.get("confidence"), int | float)
            and not isinstance(payload.get("confidence"), bool)
        ):
            error = "Vertex structured-output probe returned an invalid payload."
    except (AuthenticationError, PermissionDeniedError) as exc:
        error = f"Google credentials rejected: {exc.__class__.__name__}"
    except BadRequestError:
        error = "Vertex rejected the model, region, capacity, or probe request."
    except APITimeoutError:
        error = f"Vertex did not respond within {int(VERTEX_TEST_TIMEOUT_SECONDS)}s."
        transient = True
    except APIConnectionError:
        error = "Could not reach Vertex. Check network access and the configured region."
        transient = True
    except RateLimitError:
        error = (
            "Vertex returned HTTP 429: the project's quota or its Provisioned "
            "Throughput capacity is exhausted."
        )
        transient = True
    except APIStatusError as exc:
        error = f"Vertex returned HTTP {exc.status_code}."
        transient = exc.status_code >= 500
    except RuntimeError as exc:
        # e.g. google-genai not installed, or credential build failure.
        error = f"Vertex client could not start: {exc.__class__.__name__}."
    except ValueError as exc:
        # A key refused before any request is sent (not JSON, missing fields,
        # a token endpoint that is not Google's): the message says what to fix.
        # Bounded to the `last_validation_error` column it is stored in.
        error = str(exc)[:512]
    except Exception as exc:
        logger.error(
            "Unexpected error during Vertex test",
            extra={"source": source, "error_code": exc.__class__.__name__},
        )
        error = f"Unexpected Vertex probe error: {exc.__class__.__name__}."
    latency_ms = int((time.perf_counter() - started) * 1000)
    return VertexProbeResult(error, latency_ms, resolved_model, transient)


def record_stored_probe(
    row: ClientAIConfig | PlatformAISetting,
    result: VertexProbeResult,
    *,
    location: str,
    capacity_mode: str,
) -> None:
    """Stamp a stored credential row with a probe of exactly its saved settings.

    Success activates the row for the location, model and capacity mode that
    were probed. A definitive failure (refused key, rejected model, region or
    capacity, unusable structured output) deactivates it. A transient one only
    records the error: it says nothing about the key, and in production an
    active key going inactive switches AI off for everyone who runs on it.
    Caller commits.
    """
    if result.error is None:
        row.last_validated_at = datetime.now(tz=UTC)
        row.validated_fingerprint = row.key_fingerprint
        row.validated_model = result.model
        row.validated_location = location
        row.validated_capacity_mode = capacity_mode
        row.validation_status = "active"
    elif not result.transient:
        row.validation_status = "invalid"
    row.last_validation_error = result.error
