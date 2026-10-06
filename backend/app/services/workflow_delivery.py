"""Leased workflow mail delivery. All producers enqueue in their own transaction."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from app.core.mailer import get_mailer
from app.db.session import SessionLocal
from app.db.tenancy import is_postgres, set_search_path
from app.models import UnderwritingReview, WorkflowNotification
from app.models.underwriting_case import OPEN_REVIEW_STATUSES


def process_one_workflow_notification(broker_firm_id: str | None) -> bool:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        query = (
            select(WorkflowNotification)
            .where(
                or_(
                    (WorkflowNotification.status == "queued")
                    & (WorkflowNotification.available_at <= now),
                    (WorkflowNotification.status == "sending")
                    & (WorkflowNotification.lease_expires_at <= now),
                )
            )
            .order_by(WorkflowNotification.available_at, WorkflowNotification.created_at)
            .limit(1)
        )
        if is_postgres(db):
            query = query.with_for_update(skip_locked=True)
        row = db.scalar(query)
        if row is None:
            return False
        if row.kind == "enrollment_update":
            from app.models import Enrollment, EnrollmentEvent
            from app.services.enrollment_events import recipient_for

            event = db.get(EnrollmentEvent, row.subject_id)
            enrollment = db.get(Enrollment, event.enrollment_id) if event else None
            if (
                not enrollment
                or enrollment.client_id != row.client_id
                or recipient_for(db, enrollment) != row.recipient_email
            ):
                row.status = "cancelled"
                row.last_error = "Recipient is no longer eligible for delivery."
                row.lease_token = None
                row.lease_expires_at = None
                db.commit()
                return True
        if row.kind == "underwriting_reminder":
            review = db.get(UnderwritingReview, row.subject_id)
            if (
                not review
                or review.client_id != row.client_id
                or review.status not in OPEN_REVIEW_STATUSES
            ):
                row.status = "cancelled"
                row.lease_token = None
                row.lease_expires_at = None
                db.commit()
                return True
        row.status = "sending"
        row.attempts += 1
        row.lease_token = token = str(uuid4())
        row.lease_expires_at = now + timedelta(minutes=5)
        ident, kind, recipient, subject_id = row.id, row.kind, row.recipient_email, row.subject_id
        payload = dict(row.payload)
        db.commit()
    error = None
    try:
        if kind == "underwriting_reminder":
            get_mailer().send_workflow_notice(
                recipient,
                "Underwriting follow-up from your benefits team",
                "Your benefits team is following up on an outstanding underwriting review. "
                "Please contact your benefits team for the requirements and next steps.\n\n"
                f"Review reference: {subject_id}\n\n"
                "Medical details are not included in email.",
            )
        elif kind == "enrollment_update":
            from app.core.settings import get_settings

            if get_settings().mail_mode != "smtp":
                raise ValueError("Email delivery is not configured")
            get_mailer().send_workflow_notice(
                recipient,
                "An enrolment update is available in your benefits portal",
                "Your benefits enrolment has an update. Sign in to read the details "
                "and check whether you need to take action.\n\n"
                f"Sign in: {payload['portal_url']}\n\n"
                "Personal details and review reasons are not included in email.",
            )
        else:
            raise ValueError("Unknown workflow notification kind")
    except Exception as exc:
        error = f"{type(exc).__name__}: delivery failed"
    with SessionLocal() as db:
        set_search_path(db, broker_firm_id)
        row = db.scalar(
            select(WorkflowNotification)
            .where(
                WorkflowNotification.id == ident,
                WorkflowNotification.status == "sending",
                WorkflowNotification.lease_token == token,
            )
            .with_for_update()
        )
        if row:
            row.lease_token = None
            row.lease_expires_at = None
            row.last_error = error
            if error is None:
                row.status = "sent"
                row.sent_at = datetime.now(UTC)
            elif row.attempts >= 5:
                row.status = "dead"
            else:
                row.status = "queued"
                row.available_at = datetime.now(UTC) + timedelta(minutes=2 ** (row.attempts - 1))
            db.commit()
    return True
