"""Reminders count actual deliveries, retain history and enforce company ownership."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.v1 import underwriting_reminders
from app.api.v1.underwriting_reminders import (
    ReminderIn,
    list_reminders,
    queue_reminder,
    router,
)
from app.core.auth import CurrentUser
from app.core.deps import require_claim_configuration
from app.db.base import Base
from app.models import BrokerFirm, Client, PolicyYear, UnderwritingReview, WorkflowNotification
from app.services import workflow_delivery


@pytest.fixture
def reminders(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        db.add(Client(id="client", name="Client", broker_firm_id="firm"))
        db.flush()
        db.add(
            PolicyYear(
                id="year",
                client_id="client",
                year=2026,
                start_date=date(2026, 1, 1),
                end_date=date(2026, 12, 31),
            )
        )
        db.flush()
        db.add(
            UnderwritingReview(
                id="review",
                client_id="client",
                policy_year_id="year",
                insurer="Alpha",
                status="pending_employee",
            )
        )
        db.commit()
    sent = []

    class Mailer:
        def send_workflow_notice(self, email, subject, body):
            sent.append((email, subject, body))

    monkeypatch.setattr(workflow_delivery, "SessionLocal", factory)
    monkeypatch.setattr(workflow_delivery, "get_mailer", Mailer)
    yield factory, sent
    engine.dispose()


def user(client="client"):
    return CurrentUser(
        user_id="broker", role="broker_admin", broker_firm_id="firm", client_id=client
    )


def http_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/underwriting/reviews/review/reminders",
            "headers": [(b"x-inspro-client", b"client")],
            "client": ("testclient", 50000),
        }
    )


def reminder_input():
    return ReminderIn(
        id=uuid4(),
        recipient_email="employee@example.invalid",
        follow_up_on=date.today() + timedelta(days=7),
    )


def test_confirmed_delivery_updates_count_and_preserves_recipient_followup(reminders):
    factory, sent = reminders
    body = reminder_input()
    with factory() as db:
        result = queue_reminder(http_request(), "review", body, user(), db)
        assert result["sent_count"] == 0
        assert result["items"][0]["status"] == "queued"
        assert result["next_follow_up"] == body.follow_up_on
        again = queue_reminder(http_request(), "review", body, user(), db)
        assert len(again["items"]) == 1
        assert db.scalar(select(func.count()).select_from(WorkflowNotification)) == 1
    assert workflow_delivery.process_one_workflow_notification(None)
    assert len(sent) == 1
    with factory() as db:
        result = list_reminders("review", user(), db)
        assert result["sent_count"] == 1
        assert result["last_sent_at"] is not None
        assert result["items"][0]["recipient_email"] == body.recipient_email
        assert result["items"][0]["status"] == "sent"


def test_cross_company_and_closed_review_cannot_queue(reminders):
    factory, _ = reminders
    with factory() as db:
        with pytest.raises(HTTPException) as denied:
            queue_reminder(
                http_request(), "review", reminder_input(), user("other"), db
            )
        assert denied.value.status_code == 404
        db.get(UnderwritingReview, "review").status = "completed"
        db.commit()
        with pytest.raises(HTTPException) as closed:
            queue_reminder(http_request(), "review", reminder_input(), user(), db)
        assert closed.value.status_code == 409


def test_post_requires_admin_and_rejects_invalid_input(reminders):
    post_route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.methods == {"POST"}
    )
    assert require_claim_configuration in {
        dependency.call for dependency in post_route.dependant.dependencies
    }
    with pytest.raises(ValidationError):
        ReminderIn(id=uuid4(), recipient_email="not-an-email")
    factory, _ = reminders
    body = ReminderIn(
        id=uuid4(),
        recipient_email="member@test.invalid",
        follow_up_on=date.today() - timedelta(days=1),
    )
    with factory() as db, pytest.raises(HTTPException) as invalid_date:
        queue_reminder(http_request(), "review", body, user(), db)
    assert invalid_date.value.status_code == 422


def test_disabled_delivery_cannot_queue(reminders, monkeypatch):
    factory, _ = reminders
    monkeypatch.setattr(
        underwriting_reminders,
        "get_settings",
        lambda: type("Settings", (), {"mail_mode": "disabled"})(),
    )
    with factory() as db, pytest.raises(HTTPException) as disabled:
        queue_reminder(
            http_request(), "review", reminder_input(), user(), db
        )
    assert disabled.value.status_code == 503
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(WorkflowNotification)) == 0


def test_review_closed_after_queue_cancels_delivery(reminders):
    factory, sent = reminders
    with factory() as db:
        queue_reminder(http_request(), "review", reminder_input(), user(), db)
        db.get(UnderwritingReview, "review").status = "cancelled"
        db.commit()
    assert workflow_delivery.process_one_workflow_notification(None)
    assert sent == []
    with factory() as db:
        result = list_reminders("review", user(), db)
        assert result["sent_count"] == 0
        assert result["items"][0]["status"] == "cancelled"


def test_failed_delivery_retries_without_inflating_reminder_count(reminders, monkeypatch):
    factory, _ = reminders
    with factory() as db:
        queue_reminder(http_request(), "review", reminder_input(), user(), db)

    class Broken:
        def send_workflow_notice(self, *_):
            raise RuntimeError("private details")

    monkeypatch.setattr(workflow_delivery, "get_mailer", Broken)
    assert workflow_delivery.process_one_workflow_notification(None)
    with factory() as db:
        result = list_reminders("review", user(), db)
        assert result["sent_count"] == 0
        assert result["items"][0]["status"] == "queued"
        assert result["items"][0]["last_error"] == "RuntimeError: delivery failed"
        row = db.scalar(select(WorkflowNotification))
        row.attempts = 4
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert workflow_delivery.process_one_workflow_notification(None)
    with factory() as db:
        assert list_reminders("review", user(), db)["items"][0]["status"] == "dead"
