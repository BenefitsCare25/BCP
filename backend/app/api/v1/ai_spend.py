"""AI spend visibility + budget endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Integer, case, desc, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_broker_admin, require_client_id
from app.db.session import get_db
from app.models import AISpendLog, Client
from app.schemas.api import AIBudgetUpdate
from app.services.ai_gateway import month_start_utc, platform_month_to_date_tokens
from app.services.platform_ai_settings import resolve_platform_ai_limits

router = APIRouter(prefix="/ai-spend", tags=["ai-spend"])


@router.get("/summary", response_model=dict)
def summary(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    client_id = require_client_id(user)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")

    start = month_start_utc()
    rows = list(
        db.execute(
            select(
                AISpendLog.operation,
                func.count(AISpendLog.id),
                func.coalesce(func.sum(AISpendLog.input_tokens + AISpendLog.output_tokens), 0),
                func.coalesce(func.sum(AISpendLog.cost_estimate_usd), 0.0),
                # MTD non-cache-hit tokens, inlined so we don't need a
                # second round trip via month_to_date_tokens().
                func.coalesce(
                    func.sum(
                        case(
                            (AISpendLog.cache_hit.is_(False),
                             AISpendLog.input_tokens + AISpendLog.output_tokens),
                            else_=0,
                        )
                    ),
                    0,
                ).cast(Integer),
                # Input / output split (cache hits contribute 0 to both).
                func.coalesce(func.sum(AISpendLog.input_tokens), 0).cast(Integer),
                func.coalesce(func.sum(AISpendLog.output_tokens), 0).cast(Integer),
            )
            .where(AISpendLog.client_id == client_id, AISpendLog.created_at >= start)
            .group_by(AISpendLog.operation)
        ).all()
    )

    recent = list(
        db.execute(
            select(AISpendLog)
            .where(AISpendLog.client_id == client_id)
            .order_by(desc(AISpendLog.created_at))
            .limit(20)
        )
        .scalars()
        .all()
    )

    # Platform-wide totals (shared provider key/quota) are only meaningful — and
    # only safe to expose — to a system_admin; a broker must not see the fleet
    # aggregate across other firms.
    platform: dict = {}
    if getattr(user, "role", None) == "system_admin":
        platform = {
            "platform_month_to_date_tokens": platform_month_to_date_tokens(db),
            "platform_monthly_token_cap": resolve_platform_ai_limits(
                db
            ).platform_monthly_token_cap,
        }

    return {
        "month_to_date_tokens": int(sum(r[4] for r in rows)),
        "month_to_date_input_tokens": int(sum(r[5] for r in rows)),
        "month_to_date_output_tokens": int(sum(r[6] for r in rows)),
        "month_to_date_cost_usd": round(sum(r[3] for r in rows), 4),
        "monthly_token_budget": client.ai_monthly_token_budget,
        **platform,
        "by_operation": [
            {
                "operation": op,
                "calls": calls,
                "tokens": int(tokens),
                "input_tokens": int(inp),
                "output_tokens": int(outp),
                "cost_usd": round(cost, 4),
            }
            for op, calls, tokens, cost, _mtd, inp, outp in rows
        ],
        "recent": [
            {
                "operation": r.operation,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "cost_usd": r.cost_estimate_usd,
                "cache_hit": r.cache_hit,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent
        ],
    }


@router.put("/budget", response_model=dict)
def set_budget(
    payload: AIBudgetUpdate,
    user: CurrentUser = Depends(require_broker_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Set the tenant's monthly AI token budget. 0 = unlimited (tracking only).

    Broker-admin gated (mirrors BYOK config). Enforcement lives in the AI
    gateway's ``_check_budget``, which treats 0 as no cap.
    """
    client_id = require_client_id(user)
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    before = client.ai_monthly_token_budget
    client.ai_monthly_token_budget = payload.monthly_token_budget
    write_audit(
        db,
        user,
        action="update",
        entity_type="client_ai_budget",
        entity_id=client_id,
        before={"monthly_token_budget": before},
        after={"monthly_token_budget": payload.monthly_token_budget},
    )
    db.commit()
    return {"monthly_token_budget": payload.monthly_token_budget}
