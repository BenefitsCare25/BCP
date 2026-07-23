"""Resolve platform-wide AI limits: DB singleton → env fallback → default.

All tenants share one Vertex key/quota, so these caps are global (system-admin
scoped, not per-tenant BYOK). The DB row (set via the platform settings UI) is
the source of truth; each unset field falls back to its ``INSPRO_AI_*`` env var,
then 0 (disabled). See ``models/platform_ai_settings.py`` and
``api/v1/platform_ai_settings.py``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import PlatformAISetting
from app.models.platform_ai_settings import SINGLETON_ID

logger = logging.getLogger(__name__)

ENV_PLATFORM_CAP = "INSPRO_AI_PLATFORM_MONTHLY_TOKEN_CAP"
ENV_DEFAULT_BUDGET = "INSPRO_AI_DEFAULT_MONTHLY_TOKEN_BUDGET"
ENV_MAX_CONCURRENT = "INSPRO_AI_MAX_CONCURRENT_CALLS"


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r (expected int); ignoring", name, raw)
        return default
    return val if val >= 0 else default


def _resolve(stored: int | None, env_name: str) -> int:
    """A configured field: the stored value if set, else the env fallback."""
    if stored is not None:
        return stored if stored >= 0 else 0
    return _env_int(env_name, 0)


@dataclass(frozen=True)
class PlatformAILimits:
    platform_monthly_token_cap: int
    default_monthly_token_budget: int
    max_concurrent_calls: int


def get_platform_ai_row(db: Session) -> PlatformAISetting | None:
    return db.get(PlatformAISetting, SINGLETON_ID)


def resolve_platform_ai_limits(db: Session) -> PlatformAILimits:
    """Effective platform limits in force right now (DB row over env over 0)."""
    row = get_platform_ai_row(db)
    return PlatformAILimits(
        platform_monthly_token_cap=_resolve(
            row.platform_monthly_token_cap if row else None, ENV_PLATFORM_CAP
        ),
        default_monthly_token_budget=_resolve(
            row.default_monthly_token_budget if row else None, ENV_DEFAULT_BUDGET
        ),
        max_concurrent_calls=_resolve(
            row.max_concurrent_calls if row else None, ENV_MAX_CONCURRENT
        ),
    )
