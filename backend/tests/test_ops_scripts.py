"""Database operations helpers that need no PostgreSQL (D12/D13).

The behaviour against a real server is in test_ops_scripts_pg.py; this pins
the SQLite no-ops, the input validation that keeps role/schema names out of
injected SQL, the export's secret-column filter and the provisioning switch.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from app.core.settings import clear_settings_cache, get_settings
from app.db.roles import grant_firm_schema, runtime_grant_block
from app.db.schema_drift import check_drift
from app.db.tenancy import pending_firm_ids, provision_new_firm
from scripts.db_roles import bootstrap_statements
from scripts.export_firm import SECRET_COLUMN


@pytest.fixture
def sqlite() -> Iterator[Engine]:
    engine = create_engine("sqlite://", poolclass=StaticPool)
    yield engine
    engine.dispose()


@pytest.mark.parametrize(
    ("value", "expected"), [(None, True), ("true", True), ("false", False), ("0", False)]
)
def test_runtime_provisioning_setting(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv("INSPRO_RUNTIME_PROVISIONING", raising=False)
    else:
        monkeypatch.setenv("INSPRO_RUNTIME_PROVISIONING", value)
    clear_settings_cache()
    try:
        assert get_settings().runtime_provisioning is expected
    finally:
        clear_settings_cache()


def test_sqlite_is_a_no_op(sqlite: Engine) -> None:
    with sqlite.connect() as conn:
        assert provision_new_firm(conn, "any-firm") is True
        assert pending_firm_ids(conn) == []
        assert grant_firm_schema(conn, "firm_any") is False
        with pytest.raises(RuntimeError):
            check_drift(conn)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner": "Inspro-Owner", "app": "inspro_app"},
        {"owner": "inspro_owner", "app": "inspro_app; DROP TABLE users"},
        {"owner": "inspro_app", "app": "inspro_app"},
        {"owner": "inspro_owner", "app": "inspro_app", "owner_logins": ["x"], "app_logins": ["x"]},
        {"owner": "inspro_owner", "app": "inspro_app", "app_logins": ["bad'login"]},
    ],
)
def test_bootstrap_rejects_unsafe_or_conflicting_names(kwargs: dict[str, object]) -> None:
    args = {"owner_logins": [], "app_logins": [], **kwargs}
    with pytest.raises(ValueError):
        bootstrap_statements(args.pop("owner"), args.pop("app"), **args)  # type: ignore[arg-type]


def test_role_sql_is_safe_to_run_through_text() -> None:
    statements = bootstrap_statements(
        "inspro_owner", "inspro_app", owner_logins=["inspro-migrate"],
        app_logins=["inspro-portal"],
    )
    for sql in statements:
        assert "%" not in sql
        assert not re.search(r"(?<![:\w]):\w", sql), sql  # no accidental bind parameter
    with pytest.raises(ValueError):
        runtime_grant_block("inspro_app", 'firm_x"; DROP SCHEMA public; --')


@pytest.mark.parametrize(
    "column",
    ["password_hash", "totp_secret_enc", "recovery_codes", "token", "lease_token",
     "refresh_hash", "code_hash", "identifier_hash", "encrypted_api_key",
     "encrypted_service_account", "client_secret"],
)
def test_export_drops_secret_columns(column: str) -> None:
    assert SECRET_COLUMN.match(column)


@pytest.mark.parametrize(
    "column",
    ["ai_monthly_token_budget", "input_tokens", "password_updated_at", "key_fingerprint",
     "request_hash", "email", "scope_key"],
)
def test_export_keeps_ordinary_columns(column: str) -> None:
    assert not SECRET_COLUMN.match(column)
