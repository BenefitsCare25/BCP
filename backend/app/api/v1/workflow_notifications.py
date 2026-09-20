"""Company-scoped claim notification preferences with optimistic concurrency."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.deps import require_claim_access, require_claim_configuration, require_client_id
from app.db.session import get_db
from app.models import WorkflowNotificationSettings

router = APIRouter(prefix="/workflow-notifications", tags=["workflow-notifications"])


class NotificationSettings(BaseModel):
    claim_delivery: Literal["immediate", "digest"] = "immediate"
    digest_minutes: int = Field(default=60, ge=15, le=1440)
    revision: int = Field(default=0, ge=0)


@router.get("/settings", response_model=NotificationSettings)
def get_settings(
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> NotificationSettings:
    row = db.get(WorkflowNotificationSettings, require_client_id(user))
    return (
        NotificationSettings.model_validate(row, from_attributes=True)
        if row
        else NotificationSettings()
    )


@router.put("/settings", response_model=NotificationSettings)
def save_settings(
    body: NotificationSettings,
    user: CurrentUser = Depends(require_claim_configuration),
    db: Session = Depends(get_db),
) -> NotificationSettings:
    client_id = require_client_id(user)
    values = body.model_dump(exclude={"revision"})
    if body.revision == 0:
        db.add(WorkflowNotificationSettings(client_id=client_id, revision=1, **values))
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                409, "Notification settings changed. Reload and try again."
            ) from exc
    else:
        result = db.execute(
            update(WorkflowNotificationSettings)
            .where(
                WorkflowNotificationSettings.client_id == client_id,
                WorkflowNotificationSettings.revision == body.revision,
            )
            .values(**values, revision=body.revision + 1)
            .returning(WorkflowNotificationSettings.client_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(409, "Notification settings changed. Reload and try again.")
    write_audit(db, user, "notification_settings.saved", "client", client_id, after=values)
    db.commit()
    return get_settings(user, db)
