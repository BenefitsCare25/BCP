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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.ai_config import (
    DEFAULT_VERTEX_LOCATION,
    DEFAULT_VERTEX_MODEL,
    assert_vertex_location_writable,
    pack_vertex_secret,
)
from app.core.audit import write_platform_audit
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
from app.services.vertex_probe import (
    probe_vertex,
    project_id_from_service_account,
    record_stored_probe,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/platform-ai-settings", tags=["platform-ai-settings"])


def _credentials_out(row: PlatformAISetting | None) -> PlatformAICredentialsOut:
    if row is None or not row.encrypted_service_account:
        return PlatformAICredentialsOut(configured=False)
    if (row.provider or "vertex") != "vertex":
        return PlatformAICredentialsOut(configured=False)
    return PlatformAICredentialsOut(
        configured=True,
        provider="vertex",
        location=row.location,
        model=row.model,
        capacity_mode=row.capacity_mode,
        key_fingerprint=row.key_fingerprint,
        last_validated_at=row.last_validated_at,
        last_validation_error=row.last_validation_error,
        validated_fingerprint=row.validated_fingerprint,
        validated_model=row.validated_model,
        validated_location=row.validated_location,
        validated_capacity_mode=row.validated_capacity_mode,
        validation_status=row.validation_status,
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


def _audit(
    db: Session,
    user: CurrentUser,
    verb: str,
    entity_type: str,
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Record a change in the platform trail. Caller commits.

    The key and limits span every company, so they belong to none: a tenant
    trail would file them under whichever company the admin had selected and
    show the fleet credential's details to that company's brokers.
    """
    write_platform_audit(
        db,
        user,
        action=f"{entity_type}.{verb}",
        entity_type=entity_type,
        entity_id=SINGLETON_ID,
        detail={"before": before, "after": after},
    )


def _key_snapshot(row: PlatformAISetting) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "location": row.location,
        "model": row.model,
        "capacity_mode": row.capacity_mode,
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
    _audit(
        db,
        user,
        "create" if created else "update",
        "platform_ai_settings",
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
    # Write boundary: Singapore-only in EVERY environment, not just prod — and
    # this key is fleet-wide, so a wrong region here would move every company's
    # claim documents out of region at once.
    # Store the CANONICAL form it returns — Vertex resource paths are
    # case-sensitive, so the raw input must not be persisted.
    try:
        location = assert_vertex_location_writable(location)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    row, _created = _get_or_create(db)
    had_key = bool(row.encrypted_service_account)
    before = _key_snapshot(row) if had_key else None

    row.provider = "vertex"
    row.location = location
    row.model = model
    row.capacity_mode = payload.capacity_mode
    row.encrypted_service_account = encrypt_secret(
        pack_vertex_secret(project_id, payload.service_account_json)
    )
    row.key_fingerprint = fingerprint(payload.service_account_json)
    # New key — clear stale validation status; the operator can re-test.
    row.last_validated_at = None
    row.last_validation_error = None
    row.validated_fingerprint = None
    row.validated_model = None
    row.validated_location = None
    row.validated_capacity_mode = None
    row.validation_status = "unvalidated"
    db.flush()

    _audit(
        db,
        user,
        "update" if had_key else "create",
        "platform_ai_credentials",
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
    row.capacity_mode = None
    row.encrypted_service_account = None
    row.key_fingerprint = None
    row.last_validated_at = None
    row.last_validation_error = None
    row.validated_fingerprint = None
    row.validated_model = None
    row.validated_location = None
    row.validated_capacity_mode = None
    row.validation_status = None
    _audit(db, user, "delete", "platform_ai_credentials", before=before, after=None)
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
    capacity_mode = (
        (payload.capacity_mode if payload else None)
        or (row.capacity_mode if row else None)
        or "standard_paygo"
    )

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

    result = probe_vertex(
        location=location,
        model=model,
        service_account_json=service_account_json,
        project_id=project_id,
        source="platform",
        capacity_mode=capacity_mode,
    )

    # Draft probes may reuse the stored key, but only an explicit stored-row
    # probe is allowed to alter activation provenance.
    if payload is None and row is not None and row.encrypted_service_account:
        record_stored_probe(
            row,
            result,
            location=location or DEFAULT_VERTEX_LOCATION,
            capacity_mode=capacity_mode,
        )
        db.commit()

    return AIConfigTestResult(
        ok=result.error is None, error=result.error, latency_ms=result.latency_ms
    )
