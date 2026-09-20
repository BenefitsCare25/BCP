"""PostgreSQL release gate for the Phase 1 digest and Flex changes.

The database must be disposable and named ``inspro_phase1_test``. CI may let
this module start an ephemeral PostgreSQL 16 container; local callers can pass
``INSPRO_PHASE1_PG_TEST_URL`` instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
from app.models import (
    BrokerFirm,
    Claim,
    ClaimNotification,
    Client,
    Employee,
    FlexScheme,
    MemberAccount,
    PolicyYear,
    WorkflowNotificationSettings,
)
from app.services import claim_notifications, flex_submission

BACKEND = Path(__file__).parents[1]
DATABASE_NAME = "inspro_phase1_test"
PRE_PHASE1_REVISION = "b1c3d5e7f9a2"
PHASE1_REVISION = "c2d4e6f8a0b1"


def _migrate(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND,
        env={**os.environ, "INSPRO_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _downgrade(url: str, target: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", target],
        cwd=BACKEND,
        env={**os.environ, "INSPRO_DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def pg_url():
    url = os.environ.get("INSPRO_PHASE1_PG_TEST_URL")
    container = None
    try:
        if not url:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                pytest.skip(
                    "Set INSPRO_PHASE1_PG_TEST_URL to a disposable database, "
                    "or run with GITHUB_ACTIONS=true to use Docker."
                )
            container = f"inspro-phase1-test-{uuid4().hex}"
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--env",
                    "POSTGRES_PASSWORD=phase1-test-only",
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
                "postgresql+psycopg://postgres:phase1-test-only@"
                f"127.0.0.1:{port}/{DATABASE_NAME}"
            )
        parsed = make_url(url)
        assert parsed.database == DATABASE_NAME, "Only the disposable Phase 1 database is allowed"
        assert parsed.get_backend_name() == "postgresql"
        engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
        try:
            for attempt in range(30):
                try:
                    with engine.connect() as connection:
                        connection.execute(text("SELECT 1"))
                    break
                except sa.exc.OperationalError:
                    if attempt == 29:
                        raise
                    time.sleep(1)
        finally:
            engine.dispose()
        yield url
    finally:
        if container:
            subprocess.run(
                ["docker", "stop", container], check=True, capture_output=True, timeout=30
            )


@pytest.fixture(scope="module")
def phase1_pg(pg_url):
    engine = sa.create_engine(pg_url)
    with engine.connect() as connection:
        assert not sa.inspect(connection).get_table_names(schema="public"), "Use an empty database"
    _migrate(pg_url, PRE_PHASE1_REVISION)
    firm_id, client_id = str(uuid4()), str(uuid4())
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(BrokerFirm(id=firm_id, name="Phase 1 test firm"))
        db.flush()
        db.add(Client(id=client_id, broker_firm_id=firm_id, name="Phase 1 test company"))
        db.commit()
    provision_firm_schema(engine, firm_id)
    schema = schema_for_firm(firm_id)
    with engine.begin() as connection:
        connection.execute(text(f'DROP TABLE "{schema}".workflow_notification_settings'))
        # Present only in the preserved mixed Phase 2 workspace. Keeping this
        # conditional lets the same gate run unchanged in a Phase 1-only tree.
        connection.execute(text(f'DROP TABLE IF EXISTS "{schema}".workflow_notifications'))
        connection.execute(
            text(f'ALTER TABLE "{schema}".claim_notifications DROP COLUMN digest_key')
        )
        connection.execute(
            text(f'ALTER TABLE "{schema}".claim_notifications DROP COLUMN lease_token')
        )
    _migrate(pg_url, PHASE1_REVISION)
    try:
        yield pg_url, engine, factory, firm_id, client_id
    finally:
        engine.dispose()


def _assert_phase1_shape(engine: sa.Engine, schemas: list[str]) -> None:
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for schema in schemas:
            assert "workflow_notification_settings" in inspector.get_table_names(schema=schema)
            columns = {
                column["name"]
                for column in inspector.get_columns("claim_notifications", schema=schema)
            }
            assert {"digest_key", "lease_token"}.issubset(columns)


def test_phase1_migration_round_trip_public_and_firm(phase1_pg):
    url, engine, _, firm_id, _ = phase1_pg
    schemas = ["public", schema_for_firm(firm_id)]
    _assert_phase1_shape(engine, schemas)
    engine.dispose()
    _downgrade(url, PRE_PHASE1_REVISION)
    check = sa.create_engine(url)
    with check.connect() as connection:
        inspector = sa.inspect(connection)
        for schema in schemas:
            assert "workflow_notification_settings" not in inspector.get_table_names(schema=schema)
            columns = {
                column["name"]
                for column in inspector.get_columns("claim_notifications", schema=schema)
            }
            assert "digest_key" not in columns
            assert "lease_token" not in columns
    check.dispose()
    _migrate(url, PHASE1_REVISION)
    restored = sa.create_engine(url)
    _assert_phase1_shape(restored, schemas)
    restored.dispose()


def _seed_notification_case(factory, firm_id: str, client_id: str):
    year_id, employee_id, account_id = str(uuid4()), str(uuid4()), str(uuid4())
    staff_id = f"P1-{account_id[:8]}"
    email = f"phase1-{account_id[:8]}@test.invalid"
    with factory() as db:
        set_search_path(db, firm_id)
        year = (
            db.scalar(select(func.max(PolicyYear.year)).where(PolicyYear.client_id == client_id))
            or 2025
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
            Employee(
                id=employee_id,
                client_id=client_id,
                policy_year_id=year_id,
                staff_id=staff_id,
            )
        )
        db.add(
            MemberAccount(
                id=account_id,
                client_id=client_id,
                staff_id=staff_id,
                email=email,
            )
        )
        if db.get(WorkflowNotificationSettings, client_id) is None:
            db.add(
                WorkflowNotificationSettings(
                    client_id=client_id, claim_delivery="digest", digest_minutes=60
                )
            )
        db.flush()
        claims = []
        for index in range(2):
            claim = Claim(
                client_id=client_id,
                policy_year_id=year_id,
                employee_id=employee_id,
                submitted_by_member_id=account_id,
                claim_kind="insured",
                claim_type="GHS",
                incurred_date=date(year, 9, index + 1),
                amount_claimed=Decimal("10"),
                status="submitted",
            )
            db.add(claim)
            claims.append(claim)
        db.flush()
        available = datetime.now(UTC) - timedelta(minutes=1)
        rows = [
            ClaimNotification(
                client_id=client_id,
                claim_id=claim.id,
                source_message_id=str(uuid4()),
                recipient_email=email,
                available_at=available,
                digest_key="same-digest",
            )
            for claim in claims
        ]
        db.add_all(rows)
        db.commit()
        return tuple(row.id for row in rows), year_id, employee_id


def test_digest_group_has_one_postgres_lease_and_expired_lease_recovers(phase1_pg):
    _, _, factory, firm_id, client_id = phase1_pg
    ids, _, _ = _seed_notification_case(factory, firm_id, client_id)
    barrier = threading.Barrier(2)
    results: list[claim_notifications.LeasedNotification | None] = []

    def lease() -> None:
        with factory() as db:
            set_search_path(db, firm_id)
            barrier.wait(timeout=5)
            results.append(claim_notifications._lease_one(db))

    threads = [threading.Thread(target=lease) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    leased = [result for result in results if result is not None]
    assert len(leased) == 1
    assert set(leased[0].ids) == set(ids)

    with factory() as db:
        set_search_path(db, firm_id)
        rows = db.scalars(select(ClaimNotification).where(ClaimNotification.id.in_(ids))).all()
        assert all(row.status == "sending" and row.attempts == 1 for row in rows)
        for row in rows:
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    with factory() as db:
        set_search_path(db, firm_id)
        reclaimed = claim_notifications._lease_one(db)
    assert reclaimed is not None
    assert reclaimed.lease_token != leased[0].lease_token
    with factory() as db:
        set_search_path(db, firm_id)
        rows = db.scalars(select(ClaimNotification).where(ClaimNotification.id.in_(ids))).all()
        assert all(row.attempts == 2 for row in rows)


def test_digest_worker_delivers_only_to_test_sink_and_redacts_recipient(
    phase1_pg, monkeypatch
):
    _, _, factory, firm_id, client_id = phase1_pg
    ids, _, _ = _seed_notification_case(factory, firm_id, client_id)
    delivered: list[tuple[str, tuple[str, ...]]] = []

    class TestSink:
        def send_claim_digest(self, email: str, urls: list[str]) -> None:
            assert email.endswith("@test.invalid")
            delivered.append((email, tuple(urls)))

        def send_claim_update(self, email: str, url: str) -> None:
            raise AssertionError(f"Digest unexpectedly used immediate delivery: {email} {url}")

    monkeypatch.setattr(claim_notifications, "SessionLocal", factory)
    monkeypatch.setattr(claim_notifications, "get_mailer", TestSink)
    assert claim_notifications.process_one_claim_notification(firm_id)
    assert len(delivered) == 1
    assert len(delivered[0][1]) == 2
    assert all("claim=" in url and "claim_year=" in url for url in delivered[0][1])
    with factory() as db:
        set_search_path(db, firm_id)
        rows = db.scalars(select(ClaimNotification).where(ClaimNotification.id.in_(ids))).all()
        assert all(row.status == "sent" and row.recipient_email == "" for row in rows)


def test_simultaneous_flex_submissions_serialize_on_postgres(phase1_pg, monkeypatch):
    _, _, factory, firm_id, client_id = phase1_pg
    year_id, employee_id = str(uuid4()), str(uuid4())
    with factory() as db:
        set_search_path(db, firm_id)
        year = (
            db.scalar(select(func.max(PolicyYear.year)).where(PolicyYear.client_id == client_id))
            or 2026
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
        employee = Employee(
            id=employee_id, client_id=client_id, policy_year_id=year_id, staff_id="P1-FLEX"
        )
        db.add(employee)
        db.add(
            FlexScheme(
                id=str(uuid4()),
                policy_year_id=year_id,
                scheme={"meta": {"claim_submission_basis": "reserved"}},
            )
        )
        db.flush()
        claims = []
        for index in range(2):
            claim = Claim(
                client_id=client_id,
                policy_year_id=year_id,
                employee_id=employee_id,
                claim_kind="flex",
                claim_type="Flex",
                flex_category_name="Dental",
                incurred_date=date(year, 2, index + 1),
                amount_claimed=Decimal("100"),
                currency="SGD",
                status="draft",
            )
            db.add(claim)
            claims.append(claim)
        db.commit()
        claim_ids = tuple(claim.id for claim in claims)

    monkeypatch.setattr(
        flex_submission,
        "build_member_statement",
        lambda *_: SimpleNamespace(flex=SimpleNamespace(flex_balance=100, wallet_amount=100)),
    )
    first_locked = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    outcomes: dict[str, str] = {}

    def first() -> None:
        with factory() as db:
            set_search_path(db, firm_id)
            claim = db.get(Claim, claim_ids[0])
            employee = db.get(Employee, employee_id)
            flex_submission.assert_flex_submission_allowed(db, claim, employee)
            claim.status = "submitted"
            first_locked.set()
            assert release_first.wait(8)
            db.commit()
            outcomes["first"] = "submitted"

    def second() -> None:
        assert first_locked.wait(5)
        with factory() as db:
            set_search_path(db, firm_id)
            claim = db.get(Claim, claim_ids[1])
            employee = db.get(Employee, employee_id)
            try:
                flex_submission.assert_flex_submission_allowed(db, claim, employee)
                outcomes["second"] = "allowed"
            except HTTPException as exc:
                outcomes["second"] = str(exc.detail.get("code"))
            finally:
                second_done.set()

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_locked.wait(5)
    assert not second_done.wait(0.5), "Second submission did not wait for the wallet lock"
    release_first.set()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)
    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert outcomes == {"first": "submitted", "second": "flex_wallet_exhausted"}
