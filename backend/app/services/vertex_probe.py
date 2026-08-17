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

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    PermissionDeniedError,
)

from app.core.ai_config import (
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    AIConfig,
    assert_vertex_residency,
)

logger = logging.getLogger(__name__)

_TEST_PROMPT = "Call emit_probe with ok=true."
# Vertex's first call builds google-auth credentials + a fresh HTTP client, so
# it needs headroom beyond a bare HTTP call.
VERTEX_TEST_TIMEOUT_SECONDS = 20.0
_TEST_MAX_TOKENS = 64
_PROBE_TOOL = {
    "name": "emit_probe",
    "description": "Return the structured connectivity probe.",
    "input_schema": {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
}


def project_id_from_service_account(service_account_json: str) -> str | None:
    """Read ``project_id`` out of a service-account JSON key, or None if absent."""
    try:
        project_id = str(json.loads(service_account_json)["project_id"])
    except (ValueError, KeyError, TypeError):
        return None
    return project_id or None


def probe_vertex(
    *,
    location: str | None,
    model: str | None,
    service_account_json: str | None,
    project_id: str | None,
    source: str,
) -> tuple[str | None, int, str]:
    """Make a minimal structured Gemini call. Returns ``(error, latency_ms, model)``.

    ``error`` is None on success. Never raises: every provider failure is
    translated to a short operator-readable string, because this runs behind a
    "Test connection" button and a stack trace would be useless there.
    """
    resolved_location = (location or DEFAULT_VERTEX_LOCATION).strip() or DEFAULT_VERTEX_LOCATION
    resolved_model = (model or DEFAULT_VERTEX_MODEL).strip() or DEFAULT_VERTEX_MODEL

    if not project_id or not service_account_json:
        return (
            "Enter the GCP location and service-account JSON key to test.",
            0,
            resolved_model,
        )
    try:
        assert_vertex_residency(resolved_location)
    except RuntimeError as exc:
        return str(exc), 0, resolved_model

    from app.services.vertex_gemini import build_gemini_client

    cfg = AIConfig(
        api_key=service_account_json,
        model=resolved_model,
        base_url=None,
        provider="vertex",
        gcp_project=project_id,
        gcp_location=resolved_location,
        source=source,  # type: ignore[arg-type]
    )

    started = time.perf_counter()
    error: str | None = None
    try:
        client = build_gemini_client(cfg, timeout=VERTEX_TEST_TIMEOUT_SECONDS)
        response = client.messages.create(
            model=resolved_model,
            max_tokens=_TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
            tools=[_PROBE_TOOL],
            tool_choice={"type": "tool", "name": "emit_probe"},
        )
        block = next(
            (part for part in response.content if getattr(part, "type", None) == "tool_use"),
            None,
        )
        if block is None or getattr(block, "name", None) != "emit_probe":
            error = "Vertex responded but did not support the required structured output."
        elif getattr(block, "input", {}).get("ok") is not True:
            error = "Vertex structured-output probe returned an invalid payload."
    except (AuthenticationError, PermissionDeniedError) as exc:
        error = f"Google credentials rejected: {exc.__class__.__name__}"
    except BadRequestError:
        error = "Vertex rejected the model, region, capacity, or probe request."
    except APITimeoutError:
        error = f"Vertex did not respond within {int(VERTEX_TEST_TIMEOUT_SECONDS)}s."
    except APIConnectionError:
        error = "Could not reach Vertex. Check network access and the configured region."
    except APIStatusError as exc:
        error = f"Vertex returned HTTP {exc.status_code}."
    except RuntimeError as exc:
        # e.g. google-genai not installed, or credential build failure.
        error = f"Vertex client could not start: {exc.__class__.__name__}."
    except Exception as exc:
        logger.error(
            "Unexpected error during Vertex test",
            extra={"source": source, "error_code": exc.__class__.__name__},
        )
        error = f"Unexpected Vertex probe error: {exc.__class__.__name__}."
    latency_ms = int((time.perf_counter() - started) * 1000)
    return error, latency_ms, resolved_model
