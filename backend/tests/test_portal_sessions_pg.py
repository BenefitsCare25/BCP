"""PostgreSQL migration/rotation release gate using disposable CI data only."""
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core import sessions as SESS
from app.models import AuthSession, BrokerFirm, Client, ClientAuthPolicy

BACKEND = Path(__file__).resolve().parents[1]
DATABASE_NAME = "inspro_portal_session_test"


@pytest.fixture(scope="module")
def pg():
    url = os.environ.get("INSPRO_PORTAL_SESSION_PG_TEST_URL")
    container = None
    engine = None
    try:
        if not url:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                pytest.skip("PostgreSQL gate runs in deployment CI or with a disposable test URL")
            container = f"inspro-portal-session-test-{uuid4().hex}"
            subprocess.run([
                "docker", "run", "--detach", "--rm", "--name", container,
                "--env", "POSTGRES_PASSWORD=portal-session-test-only",
                "--env", f"POSTGRES_DB={DATABASE_NAME}",
                "--publish", "127.0.0.1::5432", "postgres:16",
            ], check=True, capture_output=True, timeout=180)
            port = subprocess.check_output(
                ["docker", "port", container, "5432/tcp"], text=True, timeout=15,
            ).strip().split(":")[-1]
            url = ("postgresql+psycopg://postgres:portal-session-test-only@"
                   f"127.0.0.1:{port}/{DATABASE_NAME}")
        parsed = make_url(url)
        assert parsed.database == DATABASE_NAME and parsed.get_backend_name() == "postgresql"
        assert parsed.host in {"127.0.0.1", "localhost"}, "Never connect to an application database"
        engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
        for attempt in range(30):
            try:
                with engine.connect() as connection:
                    connection.execute(sa.text("SELECT 1"))
                break
            except sa.exc.OperationalError:
                if attempt == 29:
                    raise
                time.sleep(1)
        with engine.connect() as connection:
            assert not sa.inspect(connection).get_table_names(schema="public"), (
                "Use an empty database"
            )

        def migrate(target):
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", target], cwd=BACKEND,
                env={**os.environ, "INSPRO_DATABASE_URL": url},
                capture_output=True, text=True, timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        migrate("e3f5a7c9d1b2")
        firm, client = str(uuid4()), str(uuid4())
        with engine.begin() as connection:
            connection.execute(sa.text(
                "INSERT INTO broker_firms (id,name) VALUES (:id,'Session CI firm')"
            ), {"id": firm})
            connection.execute(sa.text(
                "INSERT INTO clients (id,name,broker_firm_id) "
                "VALUES (:id,'Session CI company',:firm)"
            ), {"id": client, "firm": firm})
            connection.execute(sa.text(
                "INSERT INTO client_auth_policy (client_id,mfa_portal_enabled) VALUES (:id,true)"
            ), {"id": client})
        migrate("head")
        yield sessionmaker(engine, expire_on_commit=False), firm, client
    finally:
        if engine is not None:
            engine.dispose()
        if container:
            subprocess.run(["docker", "stop", container], check=True,
                           capture_output=True, timeout=30)


def test_security_migration_preserves_existing_company_policy(pg):
    factory, firm, client = pg
    with factory() as db:
        policy = db.get(ClientAuthPolicy, client)
        assert policy.mfa_portal_enabled is True
        assert policy.mfa_portal_required is False
        assert policy.mfa_hr_required is False
        assert db.get(Client, client).broker_firm_id == firm
        assert db.get(BrokerFirm, firm).name == "Session CI firm"


def test_concurrent_refresh_has_one_winner_and_revokes_replayed_family(pg):
    factory, firm, client = pg
    with factory() as db:
        issued = SESS.issue_session(db, subject_type="member", subject_id=str(uuid4()),
                                    client_id=client, broker_firm_id=firm, absolute_hours=12)
        db.commit()
    barrier = threading.Barrier(2)

    def rotate():
        with factory() as db:
            barrier.wait(timeout=10)
            result = SESS.rotate_session(db, issued.token, absolute_hours=12)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate(), range(2)))
    assert sum(result.session is not None for result in results) == 1
    assert sum(result.reuse_detected for result in results) == 1
    with factory() as db:
        rows = db.scalars(sa.select(AuthSession).where(
            AuthSession.family_id == issued.family_id,
        )).all()
        assert len(rows) == 2
        assert all(row.revoked_at is not None for row in rows)


def test_rotation_preserves_mfa_proof_expiry_and_subject_revocation(pg):
    factory, firm, client = pg
    subject = str(uuid4())
    with factory() as db:
        issued = SESS.issue_session(db, subject_type="member", subject_id=subject,
                                    client_id=client, broker_firm_id=firm, absolute_hours=12,
                                    mfa_verified=True)
        db.commit()
        child = SESS.rotate_session(db, issued.token, absolute_hours=24).session
        db.commit()
        row = db.get(AuthSession, child.session_id)
        assert row.mfa_verified is True
        assert row.expires_at == issued.expires_at
        SESS.revoke_all_for_subject(db, "member", subject)
        db.commit()
        assert SESS.rotate_session(db, child.token, absolute_hours=12).session is None
        with pytest.raises(HTTPException) as error:
            SESS.validate_access_session(db, child.session_id, subject_type="member",
                                         subject_id=subject, client_id=client, idle_minutes=30)
        assert error.value.status_code == 401
