"""Transactional enqueue and leased delivery for member claim-update email."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlencode
from uuid import uuid4

from sqlalchemy import or_, select, text
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
from app.models.workflow_notification_settings import WorkflowNotificationSettings
from app.services.member_invite import portal_sign_in_url

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
LEASE_MINUTES = 5
NOTIFIABLE_EVENTS = frozenset({EVENT_APPROVED, EVENT_NEEDS_INFO, EVENT_PAID, EVENT_REJECTED})


@dataclass(frozen=True)
class LeasedNotification:
    id: str
    recipient_email: str
    portal_url: str
    ids: tuple[str, ...]
    lease_token: str
    claim_urls: tuple[str, ...] = ()


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
    if account is None or account.client_id != claim.client_id:
        return None
    email = (account.email or "").strip()
    if not email:
        return None
    now = datetime.now(UTC)
    preferences = db.get(WorkflowNotificationSettings, claim.client_id)
    digest_key = None
    available_at = now
    if preferences and preferences.claim_delivery == "digest" and message.event != EVENT_NEEDS_INFO:
        # Fixed windows bound the wait; continuously arriving updates cannot
        # postpone an employee's digest indefinitely. Urgent requests bypass it.
        seconds = preferences.digest_minutes * 60
        window = int(now.timestamp()) // seconds
        digest_key = sha256(
            f"{claim.client_id}|{account.id}|{email.lower()}|{seconds}|{window}".encode()
        ).hexdigest()
        available_at = datetime.fromtimestamp((window + 1) * seconds, UTC)
    notification = ClaimNotification(
        client_id=claim.client_id,
        claim_id=claim.id,
        source_message_id=message.id,
        recipient_email=email,
        available_at=available_at,
        digest_key=digest_key,
    )
    db.add(notification)
    return notification


def _lease_one(db: Session) -> LeasedNotification | None:
    now = datetime.now(UTC)
    stmt = (
        select(ClaimNotification)
        .where(
            or_(
                (ClaimNotification.status == NOTIFICATION_QUEUED)
                & (ClaimNotification.available_at <= now),
                (ClaimNotification.status == NOTIFICATION_SENDING)
                & (ClaimNotification.lease_expires_at <= now),
            )
        )
        .order_by(ClaimNotification.available_at, ClaimNotification.created_at)
        .limit(1)
    )
    notification = db.execute(stmt).scalar_one_or_none()
    if notification is None:
        return None
    if is_postgres(db):
        # Lock the GROUP before locking rows. Row-first SKIP LOCKED lets two
        # workers take different rows of the same digest and send two emails.
        if not db.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"claim-email|{notification.digest_key or notification.id}"},
        ):
            db.rollback()
            return None
    group = [ClaimNotification.id == notification.id]
    if notification.digest_key:
        group = [
            ClaimNotification.digest_key == notification.digest_key,
            ClaimNotification.client_id == notification.client_id,
            ClaimNotification.recipient_email == notification.recipient_email,
        ]
    # A late-committing source message must not create a second simultaneous
    # delivery while this group already has a live lease.
    active = db.scalar(
        select(ClaimNotification.id)
        .where(
            *group,
            ClaimNotification.status == NOTIFICATION_SENDING,
            ClaimNotification.lease_expires_at > now,
        )
        .limit(1)
    )
    if active:
        db.rollback()
        return None
    rows = list(
        db.scalars(
            select(ClaimNotification)
            .where(
                *group,
                or_(
                    (ClaimNotification.status == NOTIFICATION_QUEUED)
                    & (ClaimNotification.available_at <= now),
                    (ClaimNotification.status == NOTIFICATION_SENDING)
                    & (ClaimNotification.lease_expires_at <= now),
                ),
            )
            .order_by(ClaimNotification.created_at, ClaimNotification.id)
            .execution_options(populate_existing=True)
        )
    )
    if not rows:
        db.rollback()
        return None
    client = db.get(Client, notification.client_id)
    token = str(uuid4())
    for row in rows:
        row.status = NOTIFICATION_SENDING
        row.attempts += 1
        row.lease_expires_at = now + timedelta(minutes=LEASE_MINUTES)
        row.lease_token = token
        row.last_error = None
    portal_url = portal_sign_in_url(client.slug if client else None)
    claim_years = {
        claim_id: year_id
        for claim_id, year_id in db.execute(
            select(Claim.id, Claim.policy_year_id).where(
                Claim.client_id == notification.client_id,
                Claim.id.in_([row.claim_id for row in rows]),
            )
        ).all()
    }
    claim_urls = (
        tuple(
            f"{portal_url}?{urlencode(params)}"
            for claim_id in dict.fromkeys(row.claim_id for row in rows)
            for params in [
                {
                    "claim": claim_id,
                    **({"claim_year": claim_years[claim_id]} if claim_id in claim_years else {}),
                }
            ]
        )
        if notification.digest_key
        else ()
    )
    leased = LeasedNotification(
        id=notification.id,
        ids=tuple(row.id for row in rows),
        recipient_email=notification.recipient_email,
        portal_url=portal_url,
        lease_token=token,
        claim_urls=claim_urls,
    )
    db.commit()
    return leased


def process_one_claim_notification(broker_firm_id: str | None) -> bool:
    """Deliver one outbox row; failures retry with bounded backoff."""
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        leased = _lease_one(db)
    if leased is None:
        return False
    error: str | None = None
    try:
        if leased.claim_urls:
            get_mailer().send_claim_digest(leased.recipient_email, list(leased.claim_urls))
        else:
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
        rows = db.scalars(
            select(ClaimNotification)
            .where(
                ClaimNotification.id.in_(leased.ids),
                ClaimNotification.status == NOTIFICATION_SENDING,
                ClaimNotification.lease_token == leased.lease_token,
            )
            .with_for_update()
        ).all()
        for notification in rows:
            notification.lease_expires_at = None
            notification.lease_token = None
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
