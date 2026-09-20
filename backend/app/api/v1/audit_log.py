"""Audit log read endpoint."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser, get_current_user
from app.core.deps import require_claim_access, require_client_id
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.models import AuditLog, User
from app.schemas.api import AuditLogEntry, AuditLogPage

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("/claim-workload", dependencies=[Depends(require_claim_access)])
def get_claim_workload(
    from_date: date,
    to_date: date,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from app.services.claim_workload import claim_workload

    if to_date < from_date or (to_date - from_date).days > 366:
        raise HTTPException(422, "Choose an ordered date range of at most 367 days.")
    return claim_workload(db, require_client_id(user), from_date, to_date)


@router.get("", response_model=AuditLogPage)
def list_audit_log(
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    entity_type: str | None = None,
    entity_id: str | None = Query(default=None, max_length=36),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuditLogPage:
    # Intake baselines contain medical readings for server-side comparison.
    # They are internal records, never generic company activity payloads.
    filters = [AuditLog.action != "claim.intake_suggested"]
    # System admins see everything; everyone else is scoped to their client.
    if user.role != ROLE_SYSTEM_ADMIN:
        filters.append(AuditLog.client_id == require_client_id(user))
    if entity_type:
        filters.append(AuditLog.entity_type == entity_type)
    if entity_id:
        filters.append(AuditLog.entity_id == entity_id)

    base = select(AuditLog).where(*filters).order_by(AuditLog.created_at.desc())
    count = select(func.count(AuditLog.id)).where(*filters)

    total = db.scalar(count) or 0
    rows = list(db.execute(base.limit(limit)).scalars().all())
    user_ids = {row.user_id for row in rows if row.user_id}
    actors = {
        actor.id: actor.display_name or actor.email
        for actor in (
            db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
            if user_ids
            else []
        )
    }
    return AuditLogPage(
        total=total,
        items=[
            AuditLogEntry.model_validate(row).model_copy(
                update={"actor_name": actors.get(row.user_id or "")}
            )
            for row in rows
        ],
    )
