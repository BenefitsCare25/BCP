"""PostgreSQL migration/lock regression gate, mandatory in GitHub deployment CI.

CI uses an isolated, disposable PostgreSQL container (no host volumes). Locally,
set INSPRO_WICA_PG_TEST_URL to a disposable database named inspro_wica_test.
Never point this test at an application database.
"""

import asyncio
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.db.tenancy import provision_firm_schema, schema_for_firm, set_search_path
from app.main import app
from app.models import BrokerFirm, Client, WicaIncident

BACKEND = Path(__file__).parents[1]
TABLES = ("wica_packs", "wica_documents", "wica_incidents", "wica_periods", "wica_settings")


@pytest.fixture(scope="module")
def pg_url():
    url = os.environ.get("INSPRO_WICA_PG_TEST_URL")
    container = None
    try:
        if not url:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                pytest.skip("PostgreSQL regression runs in CI, or with INSPRO_WICA_PG_TEST_URL")
            container = f"inspro-wica-test-{uuid4().hex}"
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    container,
                    "--env",
                    "POSTGRES_PASSWORD=wica-test-only",
                    "--env",
                    "POSTGRES_DB=inspro_wica_test",
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
            url = f"postgresql+psycopg://postgres:wica-test-only@127.0.0.1:{port}/inspro_wica_test"
        parsed = make_url(url)
        assert parsed.database == "inspro_wica_test", (
            "Only a disposable WICA test database is allowed"
        )
        assert parsed.get_backend_name() == "postgresql"
        engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
        try:
            for attempt in range(30):
                try:
                    with engine.connect() as connection:
                        connection.execute(sa.text("SELECT 1"))
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
            # Exact generated test container only; --rm removes its ephemeral data.
            subprocess.run(
                ["docker", "stop", container], check=True, capture_output=True, timeout=30
            )


@pytest.fixture(scope="module")
def pg(pg_url):
    def migrate(target):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", target],
            cwd=BACKEND,
            env={**os.environ, "INSPRO_DATABASE_URL": pg_url},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr

    engine = sa.create_engine(pg_url, connect_args={"options": "-c lock_timeout=2000"})
    # No resetting/reusing an unknown database, even if it has the expected name.
    with engine.connect() as connection:
        assert not sa.inspect(connection).get_table_names(schema="public"), (
            "Use an empty test database"
        )
    migrate("a0b2d4f6c8e1")
    firms = [str(uuid4()), str(uuid4())]
    clients = [str(uuid4()), str(uuid4())]
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as db:
        db.add_all([BrokerFirm(id=f, name="WICA CI firm") for f in firms])
        db.flush()
        db.add_all(
            [
                Client(id=c, broker_firm_id=f, name="WICA CI company")
                for c, f in zip(clients, firms, strict=True)
            ]
        )
        db.commit()
    # Model-based provisioning represents an existing firm; remove ONLY its
    # known-empty WICA tables to recreate the state before this additive release.
    provision_firm_schema(engine, firms[0])
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(sa.text(f'DROP TABLE "{schema_for_firm(firms[0])}"."{table}"'))
    migrate("head")
    provision_firm_schema(engine, firms[1])
    try:
        yield engine, sessions, firms, clients
    finally:
        engine.dispose()


def test_migration_existing_and_new_firms_preserve_retention_fks(pg):
    engine, _, firms, _ = pg
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        for schema in ["public", *(schema_for_firm(f) for f in firms)]:
            assert set(TABLES).issubset(inspector.get_table_names(schema=schema))
            for name in TABLES:
                model = WicaIncident.metadata.tables[name]
                actual = {c["name"]: c for c in inspector.get_columns(name, schema=schema)}
                assert set(actual) == set(model.columns.keys())
                assert all(actual[c.name]["nullable"] == c.nullable for c in model.columns)
                for fk in inspector.get_foreign_keys(name, schema=schema):
                    expected_schema = "public" if fk["referred_table"] == "clients" else schema
                    assert (fk["referred_schema"] or "public") == expected_schema
                    expected_delete = (
                        "SET NULL" if fk["constrained_columns"] == ["employee_id"] else None
                    )
                    assert fk["options"].get("ondelete") == expected_delete


def test_same_worker_upload_contention_and_company_retention(pg, monkeypatch, tmp_path):
    from app.api.v1 import wica
    from app.core.storage import LocalStorage

    engine, sessions, firms, clients = pg
    user = CurrentUser(
        user_id="wica-ci", broker_firm_id=firms[0], client_id=clients[0], role="broker_admin"
    )

    def database():
        with sessions() as db:
            set_search_path(db, firms[0])
            yield db

    storage = LocalStorage(tmp_path)
    saving, release, contending = threading.Event(), threading.Event(), threading.Event()
    original_save, original_advance = storage.save, wica.svc.advance

    def delayed_save(stream, target):
        saving.set()
        if not release.wait(8):
            raise RuntimeError("Event loop could not release the upload worker")
        return original_save(stream, target)

    def advance(*args):
        if saving.is_set():
            contending.set()
        return original_advance(*args)

    monkeypatch.setattr(storage, "save", delayed_save)
    monkeypatch.setattr(wica, "get_storage", lambda: storage)
    monkeypatch.setattr(wica.svc, "advance", advance)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = database

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            period = str(uuid4())
            response = await client.put(
                "/api/v1/wica/settings",
                json={
                    "enabled": True,
                    "revision": 0,
                    "periods": [
                        {
                            "id": period,
                            "label": "2026",
                            "start_date": "2026-01-01",
                            "end_date": "2026-12-31",
                        }
                    ],
                },
            )
            assert response.status_code == 200, response.text
            response = await client.post(
                "/api/v1/wica/incidents",
                json={
                    "id": str(uuid4()),
                    "period_id": period,
                    "employee_name": "CI employee",
                    "staff_id": "CI-1",
                    "incident_date": "2026-01-01",
                },
            )
            assert response.status_code == 201, response.text
            incident = response.json()
            path = f"/api/v1/wica/incidents/{incident['id']}/documents"

            async def send(name):
                return await client.post(
                    path,
                    data={"revision": 1, "document_id": str(uuid4())},
                    files={"file": (name, b"%PDF-1.4 test", "application/pdf")},
                )

            first = asyncio.create_task(send("first.pdf"))
            second = None
            try:
                assert await asyncio.to_thread(saving.wait, 5)
                second = asyncio.create_task(send("second.pdf"))
                assert await asyncio.to_thread(contending.wait, 5)
                # The second transaction is waiting for the first's row lock.
                # An unrelated request must still run on this SAME event loop.
                health = await asyncio.wait_for(client.get("/health"), timeout=1)
                assert health.status_code == 200
            finally:
                release.set()
                results = await asyncio.wait_for(
                    asyncio.gather(first, *([second] if second else [])), timeout=10
                )
            assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
            response = await client.get(f"/api/v1/wica/incidents/{incident['id']}")
            assert len(response.json()["documents"]) == 1
            assert response.json()["revision"] == 2
            response = await client.delete(f"/api/v1/admin/clients/{clients[0]}")
            assert response.status_code == 409, response.text
            assert "retained WICA incidents" in response.json()["detail"]
            # System admin targets the OTHER firm's empty WICA configuration,
            # despite starting with the first firm's selected search path.
            with sessions() as db:
                set_search_path(db, firms[1])
                from app.models import WicaSettings

                db.add(WicaSettings(client_id=clients[1], enabled=False))
                db.commit()
            app.dependency_overrides[get_current_user] = lambda: CurrentUser(
                user_id="wica-ci-admin",
                broker_firm_id=firms[0],
                client_id=clients[0],
                role="system_admin",
            )
            response = await client.delete(f"/api/v1/admin/clients/{clients[1]}")
            assert response.status_code == 204, response.text
            return incident["id"]

    try:
        incident_id = asyncio.run(exercise())
        path = BACKEND / "alembic/versions/b1c3d5e7f9a2_broker_wica.py"
        spec = importlib.util.spec_from_file_location("wica_pg_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                with pytest.raises(RuntimeError, match="retained incidents"):
                    migration.downgrade()
        with sessions() as db:
            set_search_path(db, firms[0])
            assert db.get(WicaIncident, incident_id) is not None
    finally:
        release.set()
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
