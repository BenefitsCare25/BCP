"""Explicit broker reminders, with auditable recipients and actual delivery history."""

import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.auth import CurrentUser
from app.core.clock import today
from app.core.deps import require_claim_access, require_claim_configuration, require_client_id
from app.core.rate_limit import limiter
from app.core.settings import get_settings
from app.db.session import get_db
from app.models import (
    Dependant,
    Employee,
    MemberAccount,
    UnderwritingReview,
    WorkflowNotification,
)
from app.models.underwriting_case import OPEN_REVIEW_STATUSES

router = APIRouter(prefix="/underwriting/reviews", tags=["underwriting-reminders"])


class ReminderIn(BaseModel):
    id: UUID
    recipient_email: str = Field(min_length=3, max_length=320)
    follow_up_on: date | None = None

    @field_validator("recipient_email")
    @classmethod
    def email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", value):
            raise ValueError("Enter one valid email address")
        return value


def load_review(db: Session, user: CurrentUser, review_id: str) -> UnderwritingReview:
    review = db.scalar(
        select(UnderwritingReview).where(
            UnderwritingReview.id == review_id,
            UnderwritingReview.client_id == require_client_id(user),
        )
    )
    if review is None:
        raise HTTPException(404, "Underwriting review not found")
    return review


def reminder_history(db: Session, review: UnderwritingReview) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(WorkflowNotification)
            .where(
                WorkflowNotification.client_id == review.client_id,
                WorkflowNotification.kind == "underwriting_reminder",
                WorkflowNotification.subject_id == review.id,
            )
            .order_by(WorkflowNotification.created_at.desc())
        )
    )
    employee = db.get(Employee, review.employee_id) if review.employee_id else None
    if review.dependant_id:
        dependant = db.get(Dependant, review.dependant_id)
        employee = db.get(Employee, dependant.employee_id) if dependant else None
    account = (
        db.get(MemberAccount, employee.member_account_id)
        if employee and employee.member_account_id
        else None
    )
    sent = [row for row in rows if row.status == "sent"]
    return {
        "delivery_enabled": get_settings().mail_mode in {"log", "smtp"},
        "suggested_email": account.email
        if account and account.client_id == review.client_id
        else None,
        "sent_count": len(sent),
        "last_sent_at": max((row.sent_at for row in sent if row.sent_at), default=None),
        "next_follow_up": next(
            (
                row.follow_up_on
                for row in rows
                if row.status not in {"cancelled", "dead"}
            ),
            None,
        ),
        "items": [
            {
                key: getattr(row, key)
                for key in (
                    "id",
                    "recipient_email",
                    "status",
                    "created_at",
                    "sent_at",
                    "follow_up_on",
                    "attempts",
                    "last_error",
                )
            }
            for row in rows
        ],
    }


@router.get("/{review_id}/reminders")
def list_reminders(
    review_id: str,
    user: CurrentUser = Depends(require_claim_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return reminder_history(db, load_review(db, user, review_id))


@router.post("/{review_id}/reminders")
@limiter.limit("10/minute")
def queue_reminder(
    request: Request,
    review_id: str,
    body: ReminderIn,
    user: CurrentUser = Depends(require_claim_configuration),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    review = load_review(db, user, review_id)
    if get_settings().mail_mode not in {"log", "smtp"}:
        raise HTTPException(503, "Outbound reminder delivery is not enabled")
    if body.follow_up_on and body.follow_up_on < today():
        raise HTTPException(422, "Next follow-up cannot be in the past")
    existing = db.get(WorkflowNotification, str(body.id))
    if existing:
        if (
            existing.client_id != review.client_id
            or existing.subject_id != review.id
            or existing.kind != "underwriting_reminder"
            or existing.recipient_email != body.recipient_email
            or existing.follow_up_on != body.follow_up_on
        ):
            raise HTTPException(409, "Reminder request ID already used")
        return reminder_history(db, review)
    if review.status not in OPEN_REVIEW_STATUSES:
        raise HTTPException(409, "Only open underwriting reviews can be reminded")
    row = WorkflowNotification(
        id=str(body.id),
        client_id=review.client_id,
        policy_year_id=review.policy_year_id,
        kind="underwriting_reminder",
        subject_id=review.id,
        dedup_key=f"uw:{body.id}",
        recipient_email=body.recipient_email,
        follow_up_on=body.follow_up_on,
        created_by=user.user_id,
        available_at=datetime.now(UTC),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Reminder was already queued; refresh its history") from exc
    write_audit(
        db,
        user,
        "underwriting.reminder_queued",
        "underwriting_review",
        review.id,
        after={"notification_id": row.id, "follow_up_on": str(body.follow_up_on or "")},
    )
    db.commit()
    return reminder_history(db, review)
