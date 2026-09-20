"""Digest delivery groups by identity, persists retries and fences old leases."""

from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.workflow_notifications import (
    NotificationSettings,
    get_settings,
    router,
    save_settings,
)
from app.core.auth import CurrentUser, get_current_user
from app.core.mailer import _claim_digest_message
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    BrokerFirm,
    Claim,
    ClaimMessage,
    ClaimNotification,
    Client,
    Employee,
    MemberAccount,
    PolicyYear,
    WorkflowNotificationSettings,
)
from app.services import claim_notifications as service


@pytest.fixture
def delivery(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(BrokerFirm(id="firm", name="Firm"))
        db.flush()
        for ident in ("one", "two"):
            db.add(Client(id=ident, name=ident, broker_firm_id="firm", slug=ident))
        db.flush()
        for ident, client in (("a", "one"), ("b", "one"), ("c", "two")):
            db.add(
                MemberAccount(
                    id=ident, client_id=client, staff_id=ident, email=f"{ident}@test.invalid"
                )
            )
        db.add_all(
            [
                WorkflowNotificationSettings(
                    client_id=ident, claim_delivery="digest", digest_minutes=60
                )
                for ident in ("one", "two")
            ]
        )
        db.commit()
    sent = []

    class Mailer:
        def send_claim_update(self, email, url):
            sent.append((email, [url], "immediate"))

        def send_claim_digest(self, email, urls):
            sent.append((email, urls, "digest"))

    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service, "get_mailer", Mailer)
    yield factory, sent
    engine.dispose()


def enqueue(db, account="a", client="one", claim_id=None, event=None):
    row = service.enqueue_claim_notification(
        db,
        Claim(id=claim_id or str(uuid4()), client_id=client, submitted_by_member_id=account),
        ClaimMessage(id=str(uuid4()), author_type="broker", event=event),
    )
    db.flush()
    return row


def ready(db):
    for row in db.scalars(select(ClaimNotification).where(ClaimNotification.status == "queued")):
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()


def test_digest_groups_distinct_claims_but_never_other_member_or_company(delivery):
    factory, sent = delivery
    with factory() as db:
        first = enqueue(db)
        second = enqueue(db)
        duplicate_thread = enqueue(db, claim_id=first.claim_id)
        other_member = enqueue(db, account="b")
        other_company = enqueue(db, account="c", client="two")
        assert first.digest_key == second.digest_key == duplicate_thread.digest_key
        assert first.digest_key not in {other_member.digest_key, other_company.digest_key}
        assert service._lease_one(db) is None  # digest window is not due
        ready(db)
    assert service.process_one_claim_notification(None)
    assert sent[0][0] == "a@test.invalid"
    assert sent[0][2] == "digest"
    assert len(sent[0][1]) == 2
    assert all(
        link.startswith(service.portal_sign_in_url("one") + "?claim=") for link in sent[0][1]
    )
    with factory() as db:
        rows = db.scalars(
            select(ClaimNotification).where(ClaimNotification.digest_key == first.digest_key)
        ).all()
        assert len(rows) == 3
        assert all(row.status == "sent" and row.recipient_email == "" for row in rows)
        assert db.get(ClaimNotification, other_member.id).status == "queued"


def test_urgent_info_requests_and_default_mode_are_immediate(delivery):
    factory, sent = delivery
    with factory() as db:
        delayed = enqueue(db)
        urgent = enqueue(db, event="needs_info")
        assert delayed.digest_key and urgent.digest_key is None
        db.commit()
    assert service.process_one_claim_notification(None)
    assert sent[0][2] == "immediate"
    with factory() as db:
        assert db.get(ClaimNotification, delayed.id).status == "queued"
        db.delete(db.get(WorkflowNotificationSettings, "one"))
        db.commit()
        ordinary = enqueue(db)
        assert ordinary.digest_key is None


def test_failure_retries_entire_digest_and_redacts_terminal_recipient(delivery, monkeypatch):
    factory, _ = delivery
    with factory() as db:
        one, two = enqueue(db), enqueue(db)
        ready(db)

    class BrokenMailer:
        def send_claim_digest(self, *_):
            raise RuntimeError("private mail provider error")

    monkeypatch.setattr(service, "get_mailer", BrokenMailer)
    assert service.process_one_claim_notification(None)
    with factory() as db:
        rows = [db.get(ClaimNotification, ident) for ident in (one.id, two.id)]
        assert all(row.status == "queued" and row.attempts == 1 for row in rows)
        assert all(row.last_error == "RuntimeError: delivery failed" for row in rows)
        for row in rows:
            row.attempts = service.MAX_ATTEMPTS - 1
        ready(db)
    assert service.process_one_claim_notification(None)
    with factory() as db:
        assert all(
            row.status == "dead" and row.recipient_email == ""
            for row in db.scalars(select(ClaimNotification))
        )


def test_expired_worker_cannot_overwrite_new_lease(delivery, monkeypatch):
    factory, _ = delivery
    with factory() as db:
        row = enqueue(db)
        ready(db)

    class ReclaimedDuringSend:
        def send_claim_digest(self, *_):
            with factory() as db:
                row_in_db = db.get(ClaimNotification, row.id)
                row_in_db.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
                db.commit()
                lease = service._lease_one(db)
                assert lease is not None

    monkeypatch.setattr(service, "get_mailer", ReclaimedDuringSend)
    assert service.process_one_claim_notification(None)
    with factory() as db:
        current = db.get(ClaimNotification, row.id)
        assert current.status == "sending"
        assert current.attempts == 2
        assert current.recipient_email  # stale worker did not acknowledge newer send


def test_notification_preferences_are_company_scoped_and_detect_stale_updates(delivery):
    factory, _ = delivery
    user = CurrentUser(
        user_id="broker", role="broker_admin", broker_firm_id="firm", client_id="one"
    )
    with factory() as db:
        initial = get_settings(user, db)
        result = save_settings(
            NotificationSettings(claim_delivery="immediate", revision=initial.revision), user, db
        )
        assert result.revision == initial.revision + 1
        assert db.get(WorkflowNotificationSettings, "two").claim_delivery == "digest"
        with pytest.raises(HTTPException) as error:
            save_settings(initial, user, db)
        assert error.value.status_code == 409


def test_digest_email_contains_only_numbered_authenticated_links():
    message = _claim_digest_message(
        "a@test.invalid", ["https://portal.invalid/sign-in?claim=one"], "benefits@test.invalid"
    )
    assert "Claim 1:" in message.get_content()
    assert "diagnosis" not in message.get_content()
    assert "sign-in?claim=one" in message.get_content()


def test_digest_link_keeps_original_claim_year(delivery):
    factory, sent = delivery
    year_id = str(uuid4())
    with factory() as db:
        db.add(
            PolicyYear(
                id=year_id,
                client_id="one",
                year=2025,
                start_date=date(2025, 1, 1),
                end_date=date(2025, 12, 31),
            )
        )
        db.flush()
        employee = Employee(client_id="one", policy_year_id=year_id, staff_id="historical")
        db.add(employee)
        db.flush()
        claim = Claim(
            client_id="one",
            policy_year_id=year_id,
            employee_id=employee.id,
            claim_kind="insured",
            claim_type="GHS",
            incurred_date=date(2025, 12, 1),
            amount_claimed=20,
            status="submitted",
        )
        db.add(claim)
        db.flush()
        enqueue(db, claim_id=claim.id)
        ready(db)
    assert service.process_one_claim_notification(None)
    params = parse_qs(urlsplit(sent[0][1][0]).query)
    assert params["claim"] == [claim.id]
    assert params["claim_year"] == [year_id]


@pytest.mark.parametrize(
    "role,read_status,write_status",
    [
        ("broker_admin", 200, 200),
        ("broker_viewer", 200, 403),
        ("client_hr", 403, 403),
    ],
)
def test_preferences_api_role_boundary(delivery, role, read_status, write_status):
    factory, _ = delivery
    app = FastAPI()
    app.include_router(router)
    user = CurrentUser(user_id="broker", role=role, broker_firm_id="firm", client_id="one")

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        assert client.get("/workflow-notifications/settings").status_code == read_status
        response = client.put(
            "/workflow-notifications/settings",
            json={
                "claim_delivery": "digest",
                "digest_minutes": 120,
                "revision": 1,
            },
        )
        assert response.status_code == write_status
