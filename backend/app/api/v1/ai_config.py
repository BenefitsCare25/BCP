"""BYOK AI provider endpoints — per-tenant encrypted endpoint + API key.

Gated to `broker_admin`. `system_admin` is intentionally excluded; if/when a
platform-operator surface is needed, it'll get its own router rather than
muddle the "which tenant am I configuring" semantics here.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.ai_config import (
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    assert_vertex_location_writable,
    pack_vertex_secret,
)
from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.crypto import encrypt_secret, fingerprint
from app.core.deps import require_broker_admin, require_client_id
from app.db.session import get_db
from app.models import AISpendLog, ClientAIConfig
from app.schemas.api import (
    AIConfigOut,
    AIConfigTestPayload,
    AIConfigTestResult,
    AIConfigUpsert,
)
from app.services.vertex_probe import probe_vertex, project_id_from_service_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-config", tags=["ai-config"])


def _mask_for_fingerprint(fp: str) -> str:
    return "••••" + fp[-4:]


@router.get("", responses={204: {"description": "No BYOK configured"}})
def get_ai_config(
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> Response:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=AIConfigOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.put("", response_model=AIConfigOut)
def put_ai_config(
    payload: AIConfigUpsert,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> AIConfigOut:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    before: dict[str, Any] | None = None
    if row is None:
        row = ClientAIConfig(client_id=client_id)
        db.add(row)
        action = "create"
    else:
        before = {
            "provider": row.provider,
            "endpoint": row.endpoint,
            "model": row.model,
            "key_fingerprint": row.key_fingerprint,
            "key_masked": _mask_for_fingerprint(row.key_fingerprint),
        }
        action = "update"

    row.provider = "vertex"
    # Store the location in `endpoint`, the Gemini model in `model`, and the
    # project + service-account JSON packed into the encrypted secret. The
    # project id is read from the SA JSON (validated in the schema).
    location = (payload.endpoint or "").strip() or DEFAULT_VERTEX_LOCATION
    model = (payload.model or "").strip() or DEFAULT_VERTEX_MODEL
    try:
        project_id = str(json.loads(payload.api_key)["project_id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Service-account JSON is missing 'project_id'.",
        ) from exc
    if not project_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Service-account JSON has an empty 'project_id'.",
        )
    # Write boundary: Singapore-only in EVERY environment, not just prod — a
    # stored row ships to prod as-is.
    # Store the CANONICAL form it returns: Vertex resource paths are
    # case-sensitive, so persisting the caller's raw "Asia-Southeast1" would
    # save a location every later call rejects.
    try:
        location = assert_vertex_location_writable(location)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    row.endpoint = location
    row.model = model
    row.encrypted_api_key = encrypt_secret(
        pack_vertex_secret(project_id, payload.api_key)
    )
    row.key_fingerprint = fingerprint(payload.api_key)
    # New key — clear stale validation status; the user can re-test.
    row.last_validated_at = None
    row.last_validation_error = None
    db.flush()

    after = {
        "provider": row.provider,
        "endpoint": row.endpoint,
        "model": row.model,
        "key_fingerprint": row.key_fingerprint,
        "key_masked": _mask_for_fingerprint(row.key_fingerprint),
    }
    write_audit(
        db,
        user,
        action=action,
        entity_type="client_ai_config",
        entity_id=row.id,
        before=before,
        after=after,
    )
    db.commit()
    db.refresh(row)
    return AIConfigOut.model_validate(row)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_config(
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> None:
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    if row is None:
        return
    snapshot = {
        "provider": row.provider,
        "endpoint": row.endpoint,
        "model": row.model,
        "key_fingerprint": row.key_fingerprint,
    }
    db.delete(row)
    write_audit(
        db,
        user,
        action="delete",
        entity_type="client_ai_config",
        entity_id=row.id,
        before=snapshot,
    )
    db.commit()


@router.post("/test", response_model=AIConfigTestResult)
def test_ai_config(
    payload: AIConfigTestPayload | None = None,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> AIConfigTestResult:
    """Make a minimal Gemini call against the supplied draft or the stored row.

    Drafts are validated in-memory and never written. Results for the stored
    row update ``last_validated_at`` / ``last_validation_error`` so the UI can
    show a freshness indicator. One ``AISpendLog`` row is written either way.
    """
    client_id = require_client_id(user)
    row = (
        db.query(ClientAIConfig)
        .filter(ClientAIConfig.client_id == client_id)
        .one_or_none()
    )
    return _run_vertex_test(db, client_id, payload, row)


def _run_vertex_test(
    db: Session,
    client_id: str,
    payload: AIConfigTestPayload | None,
    row: ClientAIConfig | None,
) -> AIConfigTestResult:
    """Minimal Gemini generate_content against a draft or the stored BYOK row.

    Vertex keeps the service-account JSON (which carries project_id) as the
    encrypted secret; location rides ``endpoint`` and the Gemini model ``model``.
    The provider call itself is `services/vertex_probe.py`, shared with the
    platform-key surface so both validate a key identically.
    """
    location = (
        (payload.endpoint if payload else None)
        or (row.endpoint if row else None)
        or DEFAULT_VERTEX_LOCATION
    ).strip()
    model = (
        (payload.model if payload else None)
        or (row.model if row else None)
        or DEFAULT_VERTEX_MODEL
    ).strip()

    service_account_json: str | None = None
    project_id: str | None = None
    if payload and payload.api_key:
        service_account_json = payload.api_key
        project_id = project_id_from_service_account(payload.api_key)
        if project_id is None:
            return AIConfigTestResult(
                ok=False,
                error="Service-account JSON is missing 'project_id'.",
                latency_ms=0,
            )
    elif row is not None:
        from app.core.crypto import decrypt_secret

        try:
            packed = json.loads(decrypt_secret(row.encrypted_api_key))
            project_id = str(packed["project_id"])
            service_account_json = str(packed["service_account"])
        except Exception:
            return AIConfigTestResult(
                ok=False,
                error="Stored Vertex credentials are unreadable.",
                latency_ms=0,
            )

    error, latency_ms, model = probe_vertex(
        location=location,
        model=model,
        service_account_json=service_account_json,
        project_id=project_id,
        source="byok",
    )

    is_stored_test = row is not None and (payload is None or payload.api_key is None)
    if is_stored_test and row is not None:
        row.last_validated_at = datetime.now(tz=UTC) if error is None else row.last_validated_at
        row.last_validation_error = error
        db.flush()

    db.add(
        AISpendLog(
            client_id=client_id,
            operation="validate_ai_config",
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_estimate_usd=0.0,
            cache_hit=False,
        )
    )
    db.commit()
    return AIConfigTestResult(ok=error is None, error=error, latency_ms=latency_ms)
