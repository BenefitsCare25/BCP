"""System-level status endpoints — AI provider config, build info."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.ai_config import load_ai_config
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.models import Client
from app.services.ai_breaker import get_breaker
from app.services.ai_cache import get_cache
from app.services.ai_gateway import month_to_date_tokens

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/ai-status", response_model=dict)
def ai_status(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Whether the AI provider is configured + cache/breaker/budget state."""
    cfg = load_ai_config(db, user.client_id)
    out: dict = {
        "configured": cfg is not None,
        "source": cfg.source if cfg else "none",
        "model": cfg.model if cfg else None,
        "cache_kind": get_cache().kind,
        "breaker_state": get_breaker().state,
    }
    if user.client_id:
        client = db.get(Client, user.client_id)
        out["month_to_date_tokens"] = month_to_date_tokens(db, user.client_id)
        out["monthly_token_budget"] = client.ai_monthly_token_budget if client else 0
    return out
