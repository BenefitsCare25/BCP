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

_TEST_PROMPT = "ping"
# Vertex's first call builds google-auth credentials + a fresh HTTP client, so
# it needs headroom beyond a bare HTTP call.
VERTEX_TEST_TIMEOUT_SECONDS = 20.0
_TEST_MAX_TOKENS = 1


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
    """Make a 1-token Gemini call. Returns ``(error, latency_ms, model)``.

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
        client.messages.create(
            model=resolved_model,
            max_tokens=_TEST_MAX_TOKENS,
            messages=[{"role": "user", "content": _TEST_PROMPT}],
        )
    except (AuthenticationError, PermissionDeniedError) as exc:
        error = f"Google credentials rejected: {exc.__class__.__name__}"
    except BadRequestError as exc:
        # Truncate whichever form we use: callers persist this into a
        # String(512) column, and a Vertex 400 that echoes the request (model
        # not found, quota project) can blow past it — on Postgres that turns
        # "Test connection" into a 500 and loses the validation status.
        error = f"Bad request: {str(getattr(exc, 'message', exc))[:200]}"
    except APITimeoutError:
        error = f"Vertex did not respond within {int(VERTEX_TEST_TIMEOUT_SECONDS)}s."
    except APIConnectionError as exc:
        error = f"Could not reach Vertex: {str(exc)[:200]}"
    except APIStatusError as exc:
        error = f"Vertex returned {exc.status_code}: {str(exc)[:200]}"
    except RuntimeError as exc:
        # e.g. google-genai not installed, or credential build failure.
        error = str(exc)[:200]
    except Exception as exc:
        logger.exception("Unexpected error during Vertex %s test", source)
        error = f"Unexpected error: {exc.__class__.__name__}: {str(exc)[:160]}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    return error, latency_ms, resolved_model
