"""PostgreSQL gate for delegated-HR claim origins in every tenant schema."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.db.tenancy import provision_firm_schema, schema_for_firm
from app.models import BrokerFirm

BACKEND = Path(__file__).parents[1]
DATABASE_NAME = "inspro_task20_test"
PREVIOUS_REVISION = "d3e5f7a9b1c2"
TASK20_REVISION = "e4f6a8c0b2d3"
REPAIR_REVISION = "f5a7c9e1b3d4"
CONSTRAINT_NAME = "ck_claims_origin_valid"


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
def task20_pg_url() -> Iterator[str]:
    url = os.environ.get("INSPRO_TASK20_PG_TEST_URL")
    container = None
    try:
        if not url:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                pytest.skip(
                    "Set INSPRO_TASK20_PG_TEST_URL to a disposable database, "
                    "or run with GITHUB_ACTIONS=true to use Docker."
                )
            container = f"inspro-task20-test-{uuid4().hex}"
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--env",
                    "POSTGRES_PASSWORD=task20-test-only",
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
                "postgresql+psycopg://postgres:task20-test-only@"
                f"127.0.0.1:{port}/{DATABASE_NAME}"
            )
        parsed = make_url(url)
        assert parsed.database == DATABASE_NAME
        assert parsed.get_backend_name() == "postgresql"
        _wait_for_postgres(url)
        yield url
    finally:
        if container:
            subprocess.run(
                ["docker", "stop", container], check=True, capture_output=True, timeout=30
            )


def _constraint(connection: sa.Connection, schema: str) -> str:
    value = connection.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = to_regclass(:table_name) AND conname = :name"
        ),
        {"table_name": f"{schema}.claims", "name": CONSTRAINT_NAME},
    )
    assert value is not None
    return str(value)


def test_hr_origin_constraint_round_trip_in_public_and_firm_schemas(
    task20_pg_url: str,
) -> None:
    _alembic(task20_pg_url, "upgrade", PREVIOUS_REVISION)
    engine = sa.create_engine(task20_pg_url)
    firm_id = str(uuid4())
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(BrokerFirm(id=firm_id, name="Task 20 migration firm"))
        db.commit()
    provision_firm_schema(engine, firm_id)
    firm_schema = schema_for_firm(firm_id)

    # Provisioning uses current metadata; put the tenant copy into the same
    # pre-migration shape as public before exercising the frozen migration.
    with engine.begin() as connection:
        connection.execute(
            text(
                f'ALTER TABLE "{firm_schema}".claims '
                f'DROP CONSTRAINT "{CONSTRAINT_NAME}", '
                f'ADD CONSTRAINT "{CONSTRAINT_NAME}" '
                "CHECK (origin IN ('portal', 'broker'))"
            )
        )

    schemas = ("public", firm_schema)
    _alembic(task20_pg_url, "upgrade", TASK20_REVISION)
    with engine.connect() as connection:
        assert all("'hr'" in _constraint(connection, schema) for schema in schemas)

    _alembic(task20_pg_url, "downgrade", PREVIOUS_REVISION)
    with engine.connect() as connection:
        assert all("'hr'" not in _constraint(connection, schema) for schema in schemas)

    _alembic(task20_pg_url, "upgrade", TASK20_REVISION)
    with engine.connect() as connection:
        assert all("'hr'" in _constraint(connection, schema) for schema in schemas)

    with engine.begin() as connection:
        connection.execute(text("SET session_replication_role = replica"))
        for index, schema in enumerate(schemas):
            connection.execute(
                text(
                    f'INSERT INTO "{schema}".claims '
                    "(id, client_id, policy_year_id, employee_id, claim_kind, "
                    "claim_type, incurred_date, amount_claimed, currency, status, "
                    "origin, intake_meta, created_at, updated_at) VALUES "
                    "(:id, :client, :year, :employee, 'insured', 'Specialist', "
                    "DATE '2026-09-20', 25, 'SGD', 'draft', 'portal', "
                    "CAST(:meta AS json), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": f"legacy-hr-{index}",
                    "client": f"legacy-client-{index}",
                    "year": f"legacy-year-{index}",
                    "employee": f"legacy-employee-{index}",
                    "meta": '{"submission_channel":"hr"}',
                },
            )
        connection.execute(text("SET session_replication_role = origin"))

    _alembic(task20_pg_url, "upgrade", REPAIR_REVISION)
    with engine.connect() as connection:
        for index, schema in enumerate(schemas):
            assert connection.scalar(
                text(f'SELECT origin FROM "{schema}".claims WHERE id = :id'),
                {"id": f"legacy-hr-{index}"},
            ) == "hr"

    _alembic(task20_pg_url, "downgrade", TASK20_REVISION)
    with engine.connect() as connection:
        for index, schema in enumerate(schemas):
            assert connection.scalar(
                text(f'SELECT origin FROM "{schema}".claims WHERE id = :id'),
                {"id": f"legacy-hr-{index}"},
            ) == "hr"
    engine.dispose()
