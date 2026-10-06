"""Record lifecycle notices and enqueue generic email in the same transaction."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, SessionTransaction

from app.core.settings import get_settings
from app.models import Client, Employee, Enrollment, MemberAccount, WorkflowNotification
from app.models.enrollment_event import EnrollmentEvent
from app.models.enrollment_form import EnrollmentFormSubmission
from app.schemas.enrollment_events import EnrollmentEventOut
from app.services.member_invite import mail_deliverable, portal_sign_in_url

EVENT_TEXT = {
    "submitted": (
        "Enrolment submitted",
        "Your choices have been submitted for review. They are not confirmed yet.",
    ),
    "returned": (
        "Enrolment returned for correction",
        "Your choices are saved. Review the reason, "
        "make corrections, then sign and send again before the deadline.",
    ),
    "cancelled": (
        "Enrolment submission cancelled",
        "Your saved choices have been cleared. "
        "Review your benefits and sign and send again before the deadline. "
        "Existing confirmed coverage has not changed.",
    ),
    "reopened": (
        "Enrolment reopened",
        "Your confirmed coverage stays in place while you "
        "review your saved choices. Sign and send again before the deadline.",
    ),
    "revised": (
        "Enrolment being revised",
        "Your previous signed form is no longer current. "
        "Review your choices and sign and send again before the deadline.",
    ),
    "confirmed": (
        "Enrolment confirmed",
        "Your benefits team has confirmed your choices. "
        "View Coverage for your recorded benefits and any outstanding requirements.",
    ),
    "deemed_kept": (
        "Enrolment period closed",
        "No new submission was confirmed. Existing coverage "
        "was retained under the period's default rules; unsent choices and leave "
        "trades were not applied.",
    ),
    "deemed_declined": (
        "Enrolment period closed",
        "The period's default rules were applied: "
        "voluntary benefits in scope were declined. Required coverage was "
        "retained. View Coverage for your benefits.",
    ),
}


def invalidate_forms(db: Session, enrollment: Enrollment, status: str) -> None:
    """Retain signed evidence unchanged; only its workflow standing changes."""
    for form in db.scalars(
        select(EnrollmentFormSubmission).where(
            EnrollmentFormSubmission.enrollment_id == enrollment.id,
            EnrollmentFormSubmission.status.in_(("submitted", "acknowledged")),
        )
    ):
        form.status = status


@dataclass
class MailboxOwners:
    transaction: SessionTransaction | None
    clients: dict[str, dict[str, set[str]]] = field(default_factory=dict)


def _mailbox_owners(db: Session, client_id: str) -> dict[str, set[str]]:
    """One column-only ownership scan per company and lifecycle transaction.

    Enrollment batches do not edit roster identities. A new transaction (including
    each worker delivery) rebuilds ownership, so changes between batches are seen.
    """
    from app.services.roster_attributes import EMAIL_KEYS, first_value

    transaction = db.get_transaction()
    cache = db.info.get("enrollment_mailbox_owners")
    if not isinstance(cache, MailboxOwners) or cache.transaction is not transaction:
        cache = MailboxOwners(transaction)
        db.info["enrollment_mailbox_owners"] = cache
    if client_id not in cache.clients:
        owners: dict[str, set[str]] = {}
        for staff_id, attributes in db.execute(select(Employee.staff_id, Employee.attribute_values)
            .where(Employee.client_id == client_id, Employee.status == "active")):
            email = (first_value(attributes or {}, EMAIL_KEYS) or "").strip().lower()
            if email:
                owners.setdefault(email, set()).add(staff_id)
        for staff_id, address in db.execute(select(MemberAccount.staff_id, MemberAccount.email)
            .where(MemberAccount.client_id == client_id)):
            if address:
                owners.setdefault(address.strip().lower(), set()).add(staff_id)
        cache.clients[client_id] = owners
    return cache.clients[client_id]


def recipient_for(db: Session, enrollment: Enrollment) -> str | None:
    employee = db.get(Employee, enrollment.employee_id)
    if not employee:
        return None
    account = db.scalar(
        select(MemberAccount).where(
            MemberAccount.client_id == enrollment.client_id,
            MemberAccount.staff_id == employee.staff_id,
            MemberAccount.status != "disabled",
        )
    )
    if not account or not account.email:
        return None
    email = account.email.strip().lower()
    from email.utils import parseaddr

    if (
        len(email) > 254
        or any(c.isspace() for c in email)
        or email.count("@") != 1
        or parseaddr(email)[1] != email
    ):
        return None
    shared = _mailbox_owners(db, enrollment.client_id).get(email, set()) - {employee.staff_id}
    return None if shared else account.email.strip()


def record_event(
    db: Session,
    enrollment: Enrollment,
    kind: str,
    *,
    reason: str | None = None,
    actor_id: str | None = None,
) -> EnrollmentEvent:
    event = EnrollmentEvent(
        client_id=enrollment.client_id,
        employee_id=enrollment.employee_id,
        enrollment_id=enrollment.id,
        kind=kind,
        reason=reason,
        actor_id=actor_id,
    )
    db.add(event)
    db.flush()
    enqueue_email(db, enrollment, event)
    return event


def enqueue_email(db: Session, enrollment: Enrollment, event: EnrollmentEvent) -> None:
    """Retry the same outbox identity; never duplicate queued or delivered mail."""
    notice = db.get(WorkflowNotification, event.notification_id) if event.notification_id else None
    if notice and notice.status in ("queued", "sending", "sent"):
        return
    if get_settings().mail_mode != "smtp" or not mail_deliverable():
        event.email_unavailable_reason = "Email delivery is not configured."
        return
    recipient = recipient_for(db, enrollment)
    if not recipient:
        event.email_unavailable_reason = "No active account with an individual email address."
    else:
        client = db.get(Client, enrollment.client_id)
        event.email_unavailable_reason = None
        if notice:
            notice.recipient_email = recipient
            notice.status = "queued"
            notice.attempts = 0
            notice.available_at = datetime.now(UTC)
            notice.last_error = None
            notice.lease_token = None
            notice.lease_expires_at = None
            return
        notice = WorkflowNotification(
            client_id=enrollment.client_id,
            policy_year_id=enrollment.policy_year_id,
            kind="enrollment_update",
            subject_id=event.id,
            dedup_key=f"enrollment:{event.id}",
            recipient_email=recipient,
            payload={"portal_url": portal_sign_in_url(client.slug if client else None)},
            created_by=event.actor_id,
            available_at=datetime.now(UTC),
        )
        db.add(notice)
        db.flush()
        event.notification_id = notice.id


def event_out(db: Session, event: EnrollmentEvent) -> EnrollmentEventOut:
    from app.models import EnrollmentWindow

    enrollment = db.get(Enrollment, event.enrollment_id)
    assert enrollment is not None
    window = db.get(EnrollmentWindow, enrollment.window_id)
    assert window is not None
    notice = db.get(WorkflowNotification, event.notification_id) if event.notification_id else None
    title, message = EVENT_TEXT[event.kind]
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return EnrollmentEventOut(
        id=event.id,
        enrollment_id=event.enrollment_id,
        kind=event.kind,
        title=title,
        message=message,
        reason=event.reason,
        created_at=utc(event.created_at),
        read_at=utc(event.read_at) if event.read_at else None,
        email_status=notice.status
        if notice and not event.email_unavailable_reason
        else "unavailable",
        email_detail=event.email_unavailable_reason or (notice.last_error if notice else None),
        window_name=window.name,
        closes_at=utc(window.closes_at),
    )
