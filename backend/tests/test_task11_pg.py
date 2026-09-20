"""PostgreSQL release gate for Task 11 underwriting reminders.

The database must be disposable and named ``inspro_task11_test``. CI may start
an ephemeral PostgreSQL 16 container; local callers can provide
``INSPRO_TASK11_PG_TEST_URL`` instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
from app.models import (
    BrokerFirm,
    Client,
    PolicyYear,
    UnderwritingReview,
    WorkflowNotification,
)
from app.services import workflow_delivery

BACKEND = Path(__file__).parents[1]
DATABASE_NAME = "inspro_task11_test"
PHASE1_REVISION = "c2d4e6f8a0b1"
TASK11_REVISION = "d3e5f7a9b1c2"


def _alembic(url: str, direction: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, target],
        cwd=BACKEND,
        env={**os.environ, "INSPRO_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _wait_for_postgres(url: str) -> None:
    engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
    try:
        for attempt in range(30):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                return
            except sa.exc.OperationalError:
                if attempt == 29:
                    raise
                time.sleep(1)
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def pg_url():
    url = os.environ.get("INSPRO_TASK11_PG_TEST_URL")
    container = None
    try:
        if not url:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                pytest.skip(
                    "Set INSPRO_TASK11_PG_TEST_URL to a disposable database, "
                    "or run with GITHUB_ACTIONS=true to use Docker."
                )
            container = f"inspro-task11-test-{uuid4().hex}"
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--env",
                    "POSTGRES_PASSWORD=task11-test-only",
                    "--env",
                    f"POSTGRES_DB={DATABASE_NAME}",
                    "--publish",
                    "127.0.0.1::5432",
                    "postgres:16",
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            port = (
                subprocess.check_output(
                    ["docker", "port", container, "5432/tcp"], text=True, timeout=15
                )
                .strip()
                .split(":")[-1]
            )
            url = (
                "postgresql+psycopg://postgres:task11-test-only@"
                f"127.0.0.1:{port}/{DATABASE_NAME}"
            )
        parsed = make_url(url)
        assert parsed.database == DATABASE_NAME, "Only the disposable Task 11 database is allowed"
        assert parsed.get_backend_name() == "postgresql"
        _wait_for_postgres(url)
        yield url
    finally:
        if container:
            subprocess.run(
                ["docker", "stop", container], check=True, capture_output=True, timeout=30
            )


@pytest.fixture(scope="module")
def task11_pg(pg_url):
    engine = sa.create_engine(pg_url)
    with engine.connect() as connection:
        assert not sa.inspect(connection).get_table_names(schema="public")
    _alembic(pg_url, "upgrade", PHASE1_REVISION)
    firm_id, client_id = str(uuid4()), str(uuid4())
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(BrokerFirm(id=firm_id, name="Task 11 test firm"))
        db.flush()
        db.add(Client(id=client_id, broker_firm_id=firm_id, name="Task 11 test company"))
        db.commit()
    provision_firm_schema(engine, firm_id)
    schema = schema_for_firm(firm_id)
    with engine.begin() as connection:
        connection.execute(text(f'DROP TABLE "{schema}".workflow_notifications'))
    _alembic(pg_url, "upgrade", TASK11_REVISION)
    try:
        yield pg_url, engine, factory, firm_id, client_id
    finally:
        engine.dispose()


def _assert_task11_shape(engine: sa.Engine, schemas: list[str]) -> None:
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for schema in schemas:
            assert "workflow_notifications" in inspector.get_table_names(schema=schema)
            columns = {
                column["name"]
                for column in inspector.get_columns("workflow_notifications", schema=schema)
            }
            assert {
                "client_id",
                "dedup_key",
                "recipient_email",
                "status",
                "available_at",
                "lease_token",
                "lease_expires_at",
                "sent_at",
                "last_error",
            }.issubset(columns)


def test_task11_migration_round_trip_public_and_firm(task11_pg):
    url, engine, _, firm_id, _ = task11_pg
    schemas = ["public", schema_for_firm(firm_id)]
    _assert_task11_shape(engine, schemas)
    _alembic(url, "downgrade", PHASE1_REVISION)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for schema in schemas:
            assert "workflow_notifications" not in inspector.get_table_names(schema=schema)
    _alembic(url, "upgrade", TASK11_REVISION)
    _assert_task11_shape(engine, schemas)


def _seed_notification(factory, firm_id: str, client_id: str, *, expired: bool = False) -> str:
    year_id, review_id, notification_id = str(uuid4()), str(uuid4()), str(uuid4())
    with factory() as db:
        set_search_path(db, firm_id)
        year = (
            db.scalar(select(func.max(PolicyYear.year)).where(PolicyYear.client_id == client_id))
            or 2089
        ) + 1
        db.add(
            PolicyYear(
                id=year_id,
                client_id=client_id,
                year=year,
                start_date=date(year, 1, 1),
                end_date=date(year, 12, 31),
            )
        )
        db.flush()
        db.add(
            UnderwritingReview(
                id=review_id,
                client_id=client_id,
                policy_year_id=year_id,
                insurer="Task 11 insurer",
                status="pending_employee",
            )
        )
        db.flush()
        now = datetime.now(UTC)
        db.add(
            WorkflowNotification(
                id=notification_id,
                client_id=client_id,
                policy_year_id=year_id,
                kind="underwriting_reminder",
                subject_id=review_id,
                dedup_key=f"uw:{notification_id}",
                recipient_email=f"task11-{notification_id[:8]}@test.invalid",
                status="sending" if expired else "queued",
                attempts=1 if expired else 0,
                available_at=now - timedelta(minutes=1),
                lease_token=str(uuid4()) if expired else None,
                lease_expires_at=now - timedelta(seconds=1) if expired else None,
            )
        )
        db.commit()
    return notification_id


def test_task11_postgres_lease_has_one_owner(task11_pg, monkeypatch):
    _, _, factory, firm_id, client_id = task11_pg
    notification_id = _seed_notification(factory, firm_id, client_id)
    entered = threading.Event()
    release = threading.Event()
    sent: list[str] = []

    class TestSink:
        def send_workflow_notice(self, email: str, _subject: str, body: str) -> None:
            assert email.endswith("@test.invalid")
            assert "Medical details are not included" in body
            sent.append(email)
            entered.set()
            assert release.wait(8)

    monkeypatch.setattr(workflow_delivery, "SessionLocal", factory)
    monkeypatch.setattr(workflow_delivery, "get_mailer", TestSink)
    results: list[bool] = []
    first = threading.Thread(
        target=lambda: results.append(
            workflow_delivery.process_one_workflow_notification(firm_id)
        )
    )
    first.start()
    assert entered.wait(5)
    second = threading.Thread(
        target=lambda: results.append(
            workflow_delivery.process_one_workflow_notification(firm_id)
        )
    )
    second.start()
    second.join(timeout=5)
    assert not second.is_alive()
    release.set()
    first.join(timeout=10)
    assert not first.is_alive()
    assert sorted(results) == [False, True]
    assert len(sent) == 1
    with factory() as db:
        set_search_path(db, firm_id)
        row = db.get(WorkflowNotification, notification_id)
        assert row.status == "sent"
        assert row.attempts == 1
        assert row.last_error is None


def test_task11_expired_lease_recovers(task11_pg, monkeypatch):
    _, _, factory, firm_id, client_id = task11_pg
    notification_id = _seed_notification(factory, firm_id, client_id, expired=True)
    sent: list[str] = []

    class TestSink:
        def send_workflow_notice(self, email: str, _subject: str, _body: str) -> None:
            assert email.endswith("@test.invalid")
            sent.append(email)

    monkeypatch.setattr(workflow_delivery, "SessionLocal", factory)
    monkeypatch.setattr(workflow_delivery, "get_mailer", TestSink)
    assert workflow_delivery.process_one_workflow_notification(firm_id)
    assert len(sent) == 1
    with factory() as db:
        set_search_path(db, firm_id)
        row = db.scalar(
            select(WorkflowNotification).where(WorkflowNotification.id == notification_id)
        )
        assert row.status == "sent"
        assert row.attempts == 2
        assert row.lease_token is None
        assert row.lease_expires_at is None
