import socket
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from scripts import run_private_migrations as runner
from scripts.run_private_migrations import redact, validate_database, validate_release


def test_release_must_match_image():
    validate_release("a" * 40, "a" * 40)
    with pytest.raises(RuntimeError):
        validate_release("b" * 40, "a" * 40)
    with pytest.raises(RuntimeError):
        validate_release("", "")


@pytest.mark.parametrize(
    "address,accepted", [("10.20.2.4", True), ("20.1.2.3", False), ("127.0.0.1", False)]
)
def test_database_must_use_expected_private_network(monkeypatch, address, accepted):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 5432))])
    args = (
        "postgresql+psycopg://test:test@db.test:5432/inspro?sslmode=require",
        "db.test",
        "10.20.0.0/16",
    )
    if accepted:
        validate_database(*args)
    else:
        with pytest.raises(RuntimeError):
            validate_database(*args)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///inspro",
        "postgresql+psycopg://db.test/inspro",
        "postgresql+psycopg://other.test/inspro?sslmode=require",
        "postgresql+psycopg://db.test/inspro?sslmode=require&host=other.test",
    ],
)
def test_reject_wrong_target_or_missing_tls(url):
    with pytest.raises(RuntimeError):
        validate_database(url, "db.test", "10.20.0.0/16")


def test_redacts_credentials_in_migration_output():
    assert (
        redact("url=secret-url password=pwd key=keyvalue", ["pwd", "secret-url", "keyvalue"])
        == "url=[redacted] password=[redacted] key=[redacted]"
    )


@pytest.mark.parametrize("locked,returncode,stages", [(False, 0, 0), (True, 1, 1), (True, 0, 2)])
def test_migration_is_serialized_and_fail_closed(monkeypatch, capsys, locked, returncode, stages):
    for key, value in {
        "INSPRO_GIT_SHA": "a" * 40,
        "INSPRO_EXPECTED_GIT_SHA": "a" * 40,
        "AZURE_CLIENT_ID": "identity",
        "INSPRO_MIGRATION_VAULT_URL": "https://test.vault.azure.net/",
        "INSPRO_MIGRATION_DATABASE_HOST": "db.test",
        "INSPRO_MIGRATION_DATABASE_NETWORK": "10.20.0.0/16",
    }.items():
        monkeypatch.setenv(key, value)
    # main sets these: register them with monkeypatch so tests restore the process environment.
    monkeypatch.setenv("INSPRO_DATABASE_URL", "")
    monkeypatch.setenv("INSPRO_AI_KEY_ENCRYPTION_KEY", "")
    database = "postgresql+psycopg://test:sensitive-password@db.test/inspro?sslmode=require"
    monkeypatch.setattr(runner, "ManagedIdentityCredential", MagicMock())
    monkeypatch.setattr(
        runner, "read_secret", lambda c, v, n: database if n == "database-url" else "sensitive-key"
    )
    monkeypatch.setattr(runner, "validate_database", MagicMock())
    engine = MagicMock()
    lock = engine.connect.return_value.__enter__.return_value
    lock.scalar.return_value = locked
    monkeypatch.setattr(runner, "create_engine", lambda *a, **kw: engine)
    run = MagicMock(
        return_value=CompletedProcess([], returncode, "sensitive-password", "sensitive-key")
    )
    monkeypatch.setattr(runner.subprocess, "run", run)
    if locked and returncode == 0:
        runner.main()
    else:
        with pytest.raises(RuntimeError):
            runner.main()
    assert run.call_count == stages
    if stages:
        assert run.call_args_list[0].args[0][2:] == ["alembic", "upgrade", "head"]
    if stages == 2:
        assert run.call_args_list[1].args[0][2:] == ["scripts.provision_tenants"]
    assert lock.execute.call_count == int(locked)
    engine.dispose.assert_called_once()
    assert "sensitive" not in capsys.readouterr().out
