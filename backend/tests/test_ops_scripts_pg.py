"""Database operations scripts (Increment 5, D12/D13) against real PostgreSQL.

Skipped unless INSPRO_OPS_PG_TEST_URL names a disposable, EMPTY database
called ``inspro_ops_test`` on a server where the URL's user may create roles:

    INSPRO_OPS_PG_TEST_URL=postgresql+psycopg://postgres:<pw>@127.0.0.1:55432/inspro_ops_test \
        uv run pytest tests/test_ops_scripts_pg.py

Roles are cluster-wide, so every role this module creates carries a random
suffix and is dropped (with everything it owns in the test database) at the
end. Never point this at an application database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

PG_URL = os.environ.get("INSPRO_OPS_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL, reason="INSPRO_OPS_PG_TEST_URL not set - PostgreSQL-only operations test"
)
BACKEND = Path(__file__).parents[1]
SECRET = "SECRET-VALUE-MUST-NOT-EXPORT"


@dataclass
class Ops:
    admin: Engine
    owner: Engine
    app: Engine
    owner_role: str
    app_role: str
    app_login: str


def _login_engine(name: str, password: str) -> Engine:
    return sa.create_engine(make_url(PG_URL).set(username=name, password=password))


def _migrate() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env={**os.environ, "INSPRO_DATABASE_URL": PG_URL},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def _drop_everything(admin: Engine, roles: list[str]) -> None:
    with admin.begin() as c:
        for (schema,) in c.execute(
            sa.text("SELECT nspname FROM pg_namespace WHERE left(nspname, 5) = 'firm_'")
        ):
            c.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        existing = [
            r for r in roles
            if c.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": r}).first()
        ]
        if existing:
            c.execute(sa.text("DROP OWNED BY " + ", ".join(f'"{r}"' for r in existing)))
        for table in sa.inspect(c).get_table_names(schema="public"):
            c.execute(sa.text(f'DROP TABLE IF EXISTS public."{table}" CASCADE'))
        for role in existing:
            c.execute(sa.text(f'DROP ROLE "{role}"'))


@pytest.fixture(scope="module")
def ops() -> Iterator[Ops]:
    from scripts.db_roles import bootstrap_statements

    url = make_url(PG_URL)
    assert url.get_backend_name() == "postgresql"
    assert url.database == "inspro_ops_test", "Only the disposable inspro_ops_test database"
    admin = sa.create_engine(PG_URL)
    with admin.connect() as c:
        assert not sa.inspect(c).get_table_names(schema="public"), "Use an empty database"
    suffix = uuid4().hex[:8]
    owner_role, app_role = f"ops_owner_{suffix}", f"ops_app_{suffix}"
    owner_login, app_login = f"ops_mig_{suffix}", f"ops_web_{suffix}"
    password = uuid4().hex
    roles = [owner_login, app_login, app_role, owner_role]
    previous_role = os.environ.get("INSPRO_DB_APP_ROLE")
    os.environ["INSPRO_DB_APP_ROLE"] = app_role
    try:
        _migrate()
        with admin.begin() as c:
            for login in (owner_login, app_login):
                c.execute(sa.text(f"CREATE ROLE \"{login}\" LOGIN PASSWORD '{password}'"))
            for statement in bootstrap_statements(
                owner_role, app_role, owner_logins=[owner_login], app_logins=[app_login]
            ):
                c.execute(sa.text(statement))
        yield Ops(
            admin=admin,
            owner=_login_engine(owner_login, password),
            app=_login_engine(app_login, password),
            owner_role=owner_role,
            app_role=app_role,
            app_login=app_login,
        )
    finally:
        if previous_role is None:
            os.environ.pop("INSPRO_DB_APP_ROLE", None)
        else:
            os.environ["INSPRO_DB_APP_ROLE"] = previous_role
        sa.create_engine(PG_URL).dispose()
        _drop_everything(admin, roles)
        admin.dispose()


def _fails(engine: Engine, sql: str, needle: str) -> None:
    with pytest.raises(sa.exc.DBAPIError) as exc, engine.begin() as c:
        c.execute(sa.text(sql))
    assert needle in str(exc.value.orig), str(exc.value.orig)


def _seed_firm(ops: Ops, label: str, storage_root: Path, *, owner: bool = False) -> dict[str, str]:
    """A firm with a company, staff user, member, credentials, a live session,
    a domain, a firm-schema audit row and one stored document."""
    from app.core.storage import LocalStorage, document_path
    from app.db.tenancy import provision_firm_schema, schema_for_firm
    from app.models import (
        AuthCredential,
        AuthMfa,
        AuthSession,
        BrokerFirm,
        Client,
        IdentityProvider,
        Invitation,
        MemberAccount,
        TenantDomain,
        User,
    )

    firm = BrokerFirm(name=f"Ops {label}", slug=f"ops-{label}-{uuid4().hex[:6]}",
                      is_platform_owner=owner)
    with Session(ops.admin, expire_on_commit=False) as db:
        db.add(firm)
        db.flush()
        client = Client(name=f"Co {label}", broker_firm_id=firm.id)
        user = User(email=f"{label}@example.test", role="broker_admin", broker_firm_id=firm.id)
        db.add_all([client, user])
        db.flush()
        member = MemberAccount(client_id=client.id, staff_id=f"S-{label}",
                               password_hash=SECRET)
        db.add_all([
            member,
            AuthCredential(user_id=user.id, broker_firm_id=firm.id, password_hash=SECRET),
            AuthMfa(subject_type="user", subject_id=user.id, totp_secret_enc=SECRET),
            AuthSession(subject_type="broker", subject_id=user.id, broker_firm_id=firm.id,
                        family_id=str(uuid4()), refresh_hash=f"{SECRET}-{label}"[:64],
                        expires_at=datetime.now(UTC) + timedelta(hours=1)),
            Invitation(email=f"new-{label}@example.test", broker_firm_id=firm.id,
                       role="broker_viewer", token=f"{SECRET}-{label}"[:64]),
            TenantDomain(broker_firm_id=firm.id, hostname=f"{firm.slug}.example.test",
                         status="active"),
            IdentityProvider(broker_firm_id=firm.id, kind="local", enabled=True),
        ])
        db.commit()
    provision_firm_schema(ops.owner, firm.id)
    with ops.app.begin() as c:
        c.execute(
            sa.text(
                f'INSERT INTO "{schema_for_firm(firm.id)}".audit_log (id, client_id, action, '
                "entity_type, cross_tenant_access, created_at, updated_at) VALUES "
                "(:id, :c, 'test.seed', 'client', false, now(), now())"
            ),
            {"id": str(uuid4()), "c": client.id},
        )
    key = document_path(firm.id, client.id, "claims", "c1", "d1", ".pdf")
    LocalStorage(storage_root).save(BytesIO(b"%PDF-1.4 test"), key)
    return {"firm": firm.id, "slug": str(firm.slug), "client": client.id, "user": user.id,
            "member": member.id, "key": key}


def _control_snapshot(engine: Engine) -> dict[str, object]:
    with engine.connect() as c:
        tables = sa.inspect(c).get_table_names(schema="public")
        counts = {t: c.execute(sa.text(f'SELECT count(*) FROM public."{t}"')).scalar()
                  for t in tables}
        firms = c.execute(sa.text("SELECT id, status FROM broker_firms ORDER BY id")).all()
        sessions = c.execute(sa.text("SELECT id, revoked_at FROM auth_sessions ORDER BY id")).all()
        domains = c.execute(sa.text("SELECT id, status FROM tenant_domains ORDER BY id")).all()
        schemas = c.execute(sa.text(
            "SELECT nspname FROM pg_namespace WHERE left(nspname, 5) = 'firm_' ORDER BY 1"
        )).scalars().all()
    return {"counts": counts, "firms": firms, "sessions": sessions, "domains": domains,
            "schemas": schemas}


def test_role_split_allows_dml_but_not_ddl(ops: Ops) -> None:
    from scripts.db_roles import verify

    with ops.admin.connect() as c:
        assert verify(c, ops.owner_role, ops.app_role, [ops.app_login]) == []
    with ops.app.begin() as c:
        assert c.execute(sa.text("SELECT current_user")).scalar() == ops.app_role
        c.execute(sa.text(
            "INSERT INTO fx_rates (id, base_currency, quote_currency, as_of_date, rate_date, "
            "rate, source, fetched_at) VALUES ('ops-t', 'SGD', 'USD', current_date, "
            "current_date, 1.0, 'test', now())"
        ))
        c.execute(sa.text("UPDATE fx_rates SET rate = 2.0 WHERE id = 'ops-t'"))
        c.execute(sa.text("DELETE FROM fx_rates WHERE id = 'ops-t'"))
        c.execute(sa.text("SELECT count(*) FROM broker_firms")).scalar()
    _fails(ops.app, "CREATE TABLE public.ops_x (id int)", "permission denied")
    _fails(ops.app, "CREATE SCHEMA firm_opsx", "permission denied")
    _fails(ops.app, "TRUNCATE fx_rates", "permission denied")
    _fails(ops.app, "DROP TABLE fx_rates", "must be owner")
    _fails(ops.app, "ALTER TABLE platform_audit_log DISABLE TRIGGER ALL", "must be owner")
    _fails(
        ops.app, "DROP TRIGGER platform_audit_log_append_only ON platform_audit_log",
        "must be owner",
    )
    _fails(ops.app, "DELETE FROM platform_audit_log", "permission denied")
    _fails(ops.app, "INSERT INTO alembic_version VALUES ('x')", "permission denied")
    _fails(ops.app, "RESET ROLE; CREATE TABLE public.ops_y (id int)", "permission denied")


def test_new_firm_schemas_get_runtime_grants(ops: Ops) -> None:
    from app.db.tenancy import provision_firm_schema, schema_for_firm

    # By the owner login: default privileges plus grant_firm_schema.
    by_owner = str(uuid4())
    # By another identity (e.g. before the split): grant_firm_schema alone.
    by_admin = str(uuid4())
    provision_firm_schema(ops.owner, by_owner)
    provision_firm_schema(ops.admin, by_admin)
    for fid in (by_owner, by_admin):
        schema = schema_for_firm(fid)
        with ops.app.begin() as c:
            c.execute(sa.text(f'SELECT count(*) FROM "{schema}".employees')).scalar()
            c.execute(sa.text(
                f'INSERT INTO "{schema}".audit_log (id, action, entity_type, '
                "cross_tenant_access, created_at, updated_at) "
                "VALUES (:id, 'x', 'x', false, now(), now())"
            ), {"id": str(uuid4())})
        _fails(ops.app, f'UPDATE "{schema}".audit_log SET action = action', "permission denied")
        _fails(ops.app, f'CREATE TABLE "{schema}".ops_x (id int)', "permission denied")
        _fails(ops.app, f'DROP SCHEMA "{schema}" CASCADE', "must be owner")
    with ops.admin.connect() as c:
        owner = c.execute(sa.text(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = :s"
        ), {"s": schema_for_firm(by_owner)}).scalar()
    assert owner == ops.owner_role
    for fid in (by_owner, by_admin):
        with ops.admin.begin() as c:
            c.execute(sa.text(f'DROP SCHEMA "{schema_for_firm(fid)}" CASCADE'))


def test_runtime_provisioning_off_leaves_firm_pending(
    ops: Ops, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.settings import clear_settings_cache
    from app.db.tenancy import (
        pending_firm_ids,
        provision_firm_schema,
        provision_new_firm,
        schema_for_firm,
        set_search_path,
    )
    from app.models import BrokerFirm

    monkeypatch.setenv("INSPRO_RUNTIME_PROVISIONING", "false")
    clear_settings_cache()
    try:
        with Session(ops.admin) as db:
            firm = BrokerFirm(name="Pending", slug=f"pending-{uuid4().hex[:6]}")
            db.add(firm)
            db.flush()
            assert provision_new_firm(db.connection(), firm.id) is False
            db.commit()
            fid = firm.id
        with ops.admin.connect() as c:
            assert fid in pending_firm_ids(c)
        with Session(ops.admin) as db, pytest.raises(HTTPException) as exc:
            set_search_path(db, fid)
        assert exc.value.status_code == 503
        assert exc.value.detail["code"] == "tenant_unavailable"
        provision_firm_schema(ops.owner, fid)  # what the migration job does
        with ops.admin.connect() as c:
            assert fid not in pending_firm_ids(c)
    finally:
        clear_settings_cache()
    with ops.admin.begin() as c:
        c.execute(sa.text(f'DROP SCHEMA "{schema_for_firm(fid)}" CASCADE'))
        c.execute(sa.text("DELETE FROM broker_firms WHERE id = :f"), {"f": fid})


def test_drift_check_passes_on_head_and_fails_after_alter(ops: Ops) -> None:
    from app.db.schema_drift import check_drift
    from app.db.tenancy import provision_firm_schema, schema_for_firm

    fid = str(uuid4())
    schema = schema_for_firm(fid)
    provision_firm_schema(ops.owner, fid)
    try:
        with ops.app.connect() as c:
            report = check_drift(c)
        assert schema in report.schemas and "public" in report.schemas
        assert report.errors == [], report.render()
        with ops.admin.begin() as c:
            table = f'"{schema}".audit_log'
            c.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN action DROP NOT NULL"))
            c.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN user_agent"))
            c.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN ops_x int NOT NULL"))
        result = subprocess.run(
            [sys.executable, "-m", "scripts.check_schema_drift"],
            cwd=BACKEND,
            env={**os.environ, "INSPRO_DATABASE_URL": PG_URL},
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        out = result.stdout
        assert f"{schema}.audit_log: nullability - action" in out
        assert f"{schema}.audit_log: missing_column - user_agent" in out
        assert f"{schema}.audit_log: extra_column - ops_x" in out
    finally:
        with ops.admin.begin() as c:
            c.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_export_manifest_and_secret_exclusion(ops: Ops, tmp_path: Path) -> None:
    from app.core.storage import LocalStorage
    from app.db.tenancy import schema_for_firm
    from scripts.export_firm import export_firm, read_verified_manifest, sha256_file

    root = tmp_path / "blobs"
    seeded = _seed_firm(ops, "exp", root)
    with ops.app.connect() as c, c.begin():
        export = export_firm(c, seeded["firm"], tmp_path / "out", include_files=True,
                             storage=LocalStorage(root))
    manifest = read_verified_manifest(export, seeded["firm"])
    by_path = {f["path"]: f for f in manifest["files"]}
    assert manifest["schema"] == schema_for_firm(seeded["firm"])
    assert by_path["control/broker_firms.jsonl"]["rows"] == 1
    assert by_path["control/users.jsonl"]["rows"] == 1
    assert by_path["control/tenant_domains.jsonl"]["rows"] == 1
    assert by_path["firm/audit_log.jsonl"]["rows"] == 1
    assert "password_hash" in by_path["control/member_accounts.jsonl"]["excluded_columns"]
    assert "password_hash" in by_path["control/auth_credentials.jsonl"]["excluded_columns"]
    assert "token" in by_path["control/invitations.jsonl"]["excluded_columns"]
    assert not any(p.startswith(("control/auth_mfa", "control/auth_sessions")) for p in by_path)
    for entry in manifest["files"]:
        assert sha256_file(export / entry["path"]) == entry["sha256"]
    every_byte = b"".join(p.read_bytes() for p in export.rglob("*") if p.is_file())
    assert SECRET.encode() not in every_byte
    assert manifest["blobs"]["count"] == 1
    assert (export / "files" / seeded["key"]).read_bytes() == b"%PDF-1.4 test"
    blob = json.loads((export / "blobs.jsonl").read_text(encoding="utf-8"))
    assert blob["key"] == seeded["key"] and len(blob["sha256"]) == 64


def test_offboard_dry_run_changes_nothing(ops: Ops, tmp_path: Path) -> None:
    from app.core.storage import LocalStorage
    from scripts.offboard_firm import run

    root = tmp_path / "blobs"
    seeded = _seed_firm(ops, "dry", root)
    before = _control_snapshot(ops.admin)
    assert run(ops.owner, seeded["firm"], storage=LocalStorage(root)) == 0
    assert _control_snapshot(ops.admin) == before
    assert (root / seeded["key"]).is_file()


def test_offboard_confirm_removes_only_that_firm(ops: Ops, tmp_path: Path) -> None:
    from app.core.storage import LocalStorage
    from app.db.tenancy import schema_for_firm
    from scripts.export_firm import export_firm
    from scripts.offboard_firm import OffboardRefused, run

    root = tmp_path / "blobs"
    storage = LocalStorage(root)
    gone = _seed_firm(ops, "gone", root)
    kept = _seed_firm(ops, "kept", root)
    platform = _seed_firm(ops, "owner", root, owner=True)
    with pytest.raises(OffboardRefused):
        run(ops.owner, platform["firm"], confirm=platform["slug"], operator="t",
            storage=storage)
    with pytest.raises(OffboardRefused):
        run(ops.owner, gone["firm"], confirm="wrong-slug", operator="t", storage=storage)
    kept_before = _control_snapshot(ops.admin)["counts"]

    assert run(ops.owner, gone["firm"], confirm=gone["slug"], operator="ops-test",
               storage=storage) == 3
    with ops.admin.connect() as c:
        status = c.execute(sa.text("SELECT status FROM broker_firms WHERE id = :f"),
                           {"f": gone["firm"]}).scalar()
        live = c.execute(sa.text(
            "SELECT count(*) FROM auth_sessions WHERE broker_firm_id = :f AND revoked_at IS NULL"
        ), {"f": gone["firm"]}).scalar()
        active = c.execute(sa.text(
            "SELECT count(*) FROM tenant_domains WHERE broker_firm_id = :f AND status = 'active'"
        ), {"f": gone["firm"]}).scalar()
    assert (status, live, active) == ("suspended", 0, 0)

    with ops.app.connect() as c, c.begin():
        export = export_firm(c, gone["firm"], tmp_path / "out", include_files=True,
                             storage=storage)
    assert run(ops.owner, gone["firm"], confirm=gone["slug"], operator="ops-test",
               export=export, storage=storage) == 0

    after = _control_snapshot(ops.admin)
    assert schema_for_firm(gone["firm"]) not in after["schemas"]
    assert schema_for_firm(kept["firm"]) in after["schemas"]
    assert not (root / gone["key"]).exists() and (root / kept["key"]).is_file()
    with ops.admin.connect() as c:
        for table, column in (("broker_firms", "id"), ("clients", "broker_firm_id"),
                              ("users", "broker_firm_id"), ("tenant_domains", "broker_firm_id"),
                              ("auth_sessions", "broker_firm_id"),
                              ("auth_credentials", "broker_firm_id")):
            left = c.execute(sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :f"),
                             {"f": gone["firm"]}).scalar()
            assert left == 0, table
        assert c.execute(sa.text("SELECT count(*) FROM member_accounts WHERE id = :m"),
                         {"m": gone["member"]}).scalar() == 0
        assert c.execute(sa.text("SELECT count(*) FROM auth_mfa WHERE subject_id = :u"),
                         {"u": gone["user"]}).scalar() == 0
        kept_rows = c.execute(sa.text(
            f'SELECT count(*) FROM "{schema_for_firm(kept["firm"])}".audit_log'
        )).scalar()
        actions = c.execute(sa.text(
            "SELECT action FROM platform_audit_log WHERE broker_firm_id = :f ORDER BY occurred_at"
        ), {"f": gone["firm"]}).scalars().all()
        for table, column, value in (("clients", "id", kept["client"]),
                                     ("users", "id", kept["user"]),
                                     ("member_accounts", "id", kept["member"]),
                                     ("auth_mfa", "subject_id", kept["user"])):
            assert c.execute(sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :v"),
                             {"v": value}).scalar() == 1, table
    assert kept_rows == 1
    assert actions == ["firm.offboard.freeze", "firm.offboard.remove_data",
                       "firm.offboard.complete"]
    assert after["counts"]["broker_firms"] == kept_before["broker_firms"] - 1
