"""Transactional enqueue and leased delivery for member claim-update email."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.mailer import get_mailer
from app.db.session import SessionLocal
from app.db.tenancy import is_postgres, set_search_path
from app.models import Claim, ClaimMessage, ClaimNotification, Client, MemberAccount
from app.models.claim_message import (
    AUTHOR_BROKER,
    EVENT_APPROVED,
    EVENT_NEEDS_INFO,
    EVENT_PAID,
    EVENT_REJECTED,
)
from app.models.claim_notification import (
    NOTIFICATION_DEAD,
    NOTIFICATION_QUEUED,
    NOTIFICATION_SENDING,
    NOTIFICATION_SENT,
)
from app.services.member_invite import portal_sign_in_url

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
LEASE_MINUTES = 5
NOTIFIABLE_EVENTS = frozenset(
    {EVENT_APPROVED, EVENT_NEEDS_INFO, EVENT_PAID, EVENT_REJECTED}
)


@dataclass(frozen=True)
class LeasedNotification:
    id: str
    recipient_email: str
    portal_url: str


def enqueue_claim_notification(
    db: Session,
    claim: Claim,
    message: ClaimMessage,
) -> ClaimNotification | None:
    """Add one generic-email outbox row in the claim transaction."""
    if message.author_type != AUTHOR_BROKER and message.event not in NOTIFIABLE_EVENTS:
        return None
    if not claim.submitted_by_member_id:
        return None
    account = db.get(MemberAccount, claim.submitted_by_member_id)
    email = (account.email or "").strip() if account is not None else ""
    if not email:
        return None
    notification = ClaimNotification(
        client_id=claim.client_id,
        claim_id=claim.id,
        source_message_id=message.id,
        recipient_email=email,
        available_at=datetime.now(UTC),
    )
    db.add(notification)
    return notification


def _lease_one(db: Session) -> LeasedNotification | None:
    now = datetime.now(UTC)
    stmt = (
        select(ClaimNotification)
        .where(
            or_(
                (
                    ClaimNotification.status == NOTIFICATION_QUEUED
                ) & (ClaimNotification.available_at <= now),
                (
                    ClaimNotification.status == NOTIFICATION_SENDING
                ) & (ClaimNotification.lease_expires_at <= now),
            )
        )
        .order_by(ClaimNotification.available_at, ClaimNotification.created_at)
        .limit(1)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True)
    notification = db.execute(stmt).scalar_one_or_none()
    if notification is None:
        return None
    client = db.get(Client, notification.client_id)
    notification.status = NOTIFICATION_SENDING
    notification.attempts += 1
    notification.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
    notification.last_error = None
    db.commit()
    return LeasedNotification(
        id=notification.id,
        recipient_email=notification.recipient_email,
        portal_url=portal_sign_in_url(client.slug if client is not None else None),
    )


def process_one_claim_notification(broker_firm_id: str | None) -> bool:
    """Deliver one outbox row; failures retry with bounded backoff."""
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        leased = _lease_one(db)
    if leased is None:
        return False
    error: str | None = None
    try:
        get_mailer().send_claim_update(leased.recipient_email, leased.portal_url)
    except Exception as exc:
        error = f"{type(exc).__name__}: delivery failed"[:255]
        logger.warning(
            "Claim notification delivery failed",
            extra={
                "notification_id": leased.id,
                "error_code": type(exc).__name__,
            },
        )
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        notification = db.get(ClaimNotification, leased.id)
        if notification is None or notification.status != NOTIFICATION_SENDING:
            return True
        notification.lease_expires_at = None
        if error is None:
            notification.status = NOTIFICATION_SENT
            notification.sent_at = datetime.now(UTC)
            notification.recipient_email = ""
        elif notification.attempts >= MAX_ATTEMPTS:
            notification.status = NOTIFICATION_DEAD
            notification.last_error = error
            notification.recipient_email = ""
        else:
            notification.status = NOTIFICATION_QUEUED
            notification.last_error = error
            notification.available_at = datetime.now(UTC) + timedelta(
                minutes=2 ** (notification.attempts - 1)
            )
        db.commit()
    return True
