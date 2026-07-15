"""AI spend visibility endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import Integer, case, desc, func, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.db.session import get_db
from app.models import AISpendLog, Client
from app.services.ai_gateway import month_start_utc

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

    return {
        "month_to_date_tokens": int(sum(r[4] for r in rows)),
        "month_to_date_cost_usd": round(sum(r[3] for r in rows), 4),
        "monthly_token_budget": client.ai_monthly_token_budget,
        "by_operation": [
            {"operation": op, "calls": calls, "tokens": int(tokens), "cost_usd": round(cost, 4)}
            for op, calls, tokens, cost, _mtd in rows
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
