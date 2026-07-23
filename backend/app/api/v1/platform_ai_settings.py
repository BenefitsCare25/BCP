"""Platform-wide AI limits — system-admin operator surface.

These caps span EVERY client (all tenants share one Vertex key/quota), so they
are NOT per-tenant BYOK config (`ai_config.py`, `broker_admin`-gated) — they get
their own router, gated to `system_admin`, and persist a single global row. The
per-company monthly budget stays on the AI usage tile (`/ai-spend/budget`).
See `services/platform_ai_settings.py`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import require_system_admin
from app.db.session import get_db
from app.models import PlatformAISetting
from app.models.platform_ai_settings import SINGLETON_ID
from app.schemas.api import PlatformAISettingsOut, PlatformAISettingsUpdate
from app.services.platform_ai_settings import resolve_platform_ai_limits

router = APIRouter(prefix="/platform-ai-settings", tags=["platform-ai-settings"])


def _effective(db: Session) -> PlatformAISettingsOut:
    limits = resolve_platform_ai_limits(db)
    return PlatformAISettingsOut(
        platform_monthly_token_cap=limits.platform_monthly_token_cap,
        default_monthly_token_budget=limits.default_monthly_token_budget,
        max_concurrent_calls=limits.max_concurrent_calls,
    )


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
    row = db.get(PlatformAISetting, SINGLETON_ID)
    before: dict | None = None
    if row is None:
        row = PlatformAISetting(id=SINGLETON_ID)
        db.add(row)
        action = "create"
    else:
        before = {
            "platform_monthly_token_cap": row.platform_monthly_token_cap,
            "default_monthly_token_budget": row.default_monthly_token_budget,
            "max_concurrent_calls": row.max_concurrent_calls,
        }
        action = "update"
    row.platform_monthly_token_cap = payload.platform_monthly_token_cap
    row.default_monthly_token_budget = payload.default_monthly_token_budget
    row.max_concurrent_calls = payload.max_concurrent_calls
    write_audit(
        db,
        user,
        action=action,
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
