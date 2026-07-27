"""Platform-wide AI setup — system-admin operator surface.

The PLATFORM KEY is the default every company runs on: set it once here and AI
works fleet-wide. Per-company BYOK (`ai_config.py`, `broker_admin`-gated) stays
an optional override on top — resolution order is BYOK → platform → env, in
`core/ai_config.py`.

The caps here likewise span EVERY client (all tenants share one Vertex
key/quota), which is why they are not per-tenant config. The per-company monthly
budget stays on the AI usage tile (`/ai-spend/budget`).
See `services/platform_ai_settings.py`.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.ai_config import (
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    assert_vertex_residency,
    pack_vertex_secret,
)
from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.crypto import encrypt_secret, fingerprint
from app.core.deps import require_system_admin
from app.db.session import get_db
from app.models import PlatformAISetting
from app.models.platform_ai_settings import SINGLETON_ID
from app.schemas.api import (
    AIConfigTestResult,
    PlatformAICredentialsOut,
    PlatformAICredentialsTestPayload,
    PlatformAICredentialsUpsert,
    PlatformAISettingsOut,
    PlatformAISettingsUpdate,
)
from app.services.platform_ai_settings import resolve_platform_ai_limits
from app.services.vertex_probe import probe_vertex, project_id_from_service_account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform-ai-settings", tags=["platform-ai-settings"])


def _credentials_out(row: PlatformAISetting | None) -> PlatformAICredentialsOut:
    if row is None or not row.encrypted_service_account:
        return PlatformAICredentialsOut(configured=False)
    return PlatformAICredentialsOut(
        configured=True,
        provider=row.provider or "vertex",
        location=row.location,
        model=row.model,
        key_fingerprint=row.key_fingerprint,
        last_validated_at=row.last_validated_at,
        last_validation_error=row.last_validation_error,
    )


def _effective(db: Session) -> PlatformAISettingsOut:
    limits = resolve_platform_ai_limits(db)
    return PlatformAISettingsOut(
        platform_monthly_token_cap=limits.platform_monthly_token_cap,
        default_monthly_token_budget=limits.default_monthly_token_budget,
        max_concurrent_calls=limits.max_concurrent_calls,
        credentials=_credentials_out(db.get(PlatformAISetting, SINGLETON_ID)),
    )


def _get_or_create(db: Session) -> tuple[PlatformAISetting, bool]:
    """The singleton row + whether this call created it (drives the audit action)."""
    row = db.get(PlatformAISetting, SINGLETON_ID)
    if row is not None:
        return row, False
    row = PlatformAISetting(id=SINGLETON_ID)
    db.add(row)
    return row, True


def _key_snapshot(row: PlatformAISetting) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "location": row.location,
        "model": row.model,
        "key_fingerprint": row.key_fingerprint,
    }


@router.get("", response_model=PlatformAISettingsOut)
def get_platform_ai_settings(
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformAISettingsOut:
    return _effective(db)


@router.put("", response_model=PlatformAISettingsOut)
def put_platform_ai_settings(
    payload: PlatformAISettingsUpdate,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformAISettingsOut:
    """Set the platform limits. The stored key is left untouched."""
    row, created = _get_or_create(db)
    before = (
        None
        if created
        else {
            "platform_monthly_token_cap": row.platform_monthly_token_cap,
            "default_monthly_token_budget": row.default_monthly_token_budget,
            "max_concurrent_calls": row.max_concurrent_calls,
        }
    )
    row.platform_monthly_token_cap = payload.platform_monthly_token_cap
    row.default_monthly_token_budget = payload.default_monthly_token_budget
    row.max_concurrent_calls = payload.max_concurrent_calls
    write_audit(
        db,
        user,
        action="create" if created else "update",
        entity_type="platform_ai_settings",
        entity_id=SINGLETON_ID,
        before=before,
        after={
            "platform_monthly_token_cap": row.platform_monthly_token_cap,
            "default_monthly_token_budget": row.default_monthly_token_budget,
            "max_concurrent_calls": row.max_concurrent_calls,
        },
    )
    db.commit()
    return _effective(db)


@router.put("/credentials", response_model=PlatformAISettingsOut)
def put_platform_ai_credentials(
    payload: PlatformAICredentialsUpsert,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformAISettingsOut:
    """Store the platform Vertex key (encrypted). Limits are left untouched."""
    location = (payload.location or "").strip() or DEFAULT_VERTEX_LOCATION
    model = (payload.model or "").strip() or DEFAULT_VERTEX_MODEL
    project_id = project_id_from_service_account(payload.service_account_json)
    if not project_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Service-account JSON is missing 'project_id'.",
        )
    try:
        assert_vertex_residency(location)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    row, _created = _get_or_create(db)
    had_key = bool(row.encrypted_service_account)
    before = _key_snapshot(row) if had_key else None

    row.provider = "vertex"
    row.location = location
    row.model = model
    row.encrypted_service_account = encrypt_secret(
        pack_vertex_secret(project_id, payload.service_account_json)
    )
    row.key_fingerprint = fingerprint(payload.service_account_json)
    # New key — clear stale validation status; the operator can re-test.
    row.last_validated_at = None
    row.last_validation_error = None
    db.flush()

    write_audit(
        db,
        user,
        action="update" if had_key else "create",
        entity_type="platform_ai_credentials",
        entity_id=SINGLETON_ID,
        before=before,
        after=_key_snapshot(row),
    )
    db.commit()
    return _effective(db)


@router.delete("/credentials", response_model=PlatformAISettingsOut)
def delete_platform_ai_credentials(
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformAISettingsOut:
    """Clear the platform key. Limits survive; AI falls back to BYOK/env."""
    row = db.get(PlatformAISetting, SINGLETON_ID)
    if row is None or not row.encrypted_service_account:
        return _effective(db)
    before = _key_snapshot(row)
    row.provider = None
    row.location = None
    row.model = None
    row.encrypted_service_account = None
    row.key_fingerprint = None
    row.last_validated_at = None
    row.last_validation_error = None
    write_audit(
        db,
        user,
        action="delete",
        entity_type="platform_ai_credentials",
        entity_id=SINGLETON_ID,
        before=before,
    )
    db.commit()
    return _effective(db)


@router.post("/credentials/test", response_model=AIConfigTestResult)
def test_platform_ai_credentials(
    payload: PlatformAICredentialsTestPayload | None = None,
    user: CurrentUser = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AIConfigTestResult:
    """Minimal Gemini call against the supplied draft or the stored platform key.

    Drafts are validated in-memory and never written. Testing the STORED key
    updates ``last_validated_at`` / ``last_validation_error`` so the UI can show
    freshness. No ``AISpendLog`` row is written: that table is tenant-scoped and
    the platform key has no owning client — the probe costs one token.
    """
    row = db.get(PlatformAISetting, SINGLETON_ID)
    draft_key = payload.service_account_json if payload else None
    location = (payload.location if payload else None) or (row.location if row else None)
    model = (payload.model if payload else None) or (row.model if row else None)

    service_account_json: str | None = None
    project_id: str | None = None
    if draft_key:
        service_account_json = draft_key
        project_id = project_id_from_service_account(draft_key)
        if project_id is None:
            return AIConfigTestResult(
                ok=False,
                error="Service-account JSON is missing 'project_id'.",
                latency_ms=0,
            )
    elif row is not None and row.encrypted_service_account:
        from app.core.crypto import decrypt_secret

        try:
            packed = json.loads(decrypt_secret(row.encrypted_service_account))
            project_id = str(packed["project_id"])
            service_account_json = str(packed["service_account"])
        except Exception:
            logger.exception("Stored platform Vertex credentials are unreadable")
            return AIConfigTestResult(
                ok=False,
                error="Stored Vertex credentials are unreadable.",
                latency_ms=0,
            )

    error, latency_ms, _model = probe_vertex(
        location=location,
        model=model,
        service_account_json=service_account_json,
        project_id=project_id,
        source="platform",
    )

    if not draft_key and row is not None and row.encrypted_service_account:
        if error is None:
            row.last_validated_at = datetime.now(tz=UTC)
        row.last_validation_error = error
        db.commit()

    return AIConfigTestResult(ok=error is None, error=error, latency_ms=latency_ms)
