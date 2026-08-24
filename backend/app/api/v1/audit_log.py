"""Audit log read endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import ROLE_SYSTEM_ADMIN, CurrentUser, get_current_user
from app.core.deps import require_client_id
from app.core.pagination import MAX_LIMIT
from app.db.session import get_db
from app.models import AuditLog, User
from app.schemas.api import AuditLogEntry, AuditLogPage

router = APIRouter(prefix="/audit-log", tags=["audit-log"])


@router.get("", response_model=AuditLogPage)
def list_audit_log(
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    entity_type: str | None = None,
    entity_id: str | None = Query(default=None, max_length=36),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AuditLogPage:
    filters = []
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
