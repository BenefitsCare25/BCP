"""The additive policy migration preserves accounts and authenticator records."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_broker_mfa_policy_migration_preserves_existing_data(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/a5c7e9b1d3f6_broker_mfa_policy.py"
    spec = importlib.util.spec_from_file_location("broker_mfa_policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
        connection.exec_driver_sql("INSERT INTO users VALUES ('existing', 'kept@example.test')")
        connection.exec_driver_sql("CREATE TABLE auth_mfa (subject_id TEXT, totp_secret_enc TEXT)")
        connection.exec_driver_sql(
            "INSERT INTO auth_mfa VALUES ('existing', 'retained-test-value')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql(
                "SELECT email, broker_mfa_required FROM users WHERE id='existing'"
            ).one() == ("kept@example.test", 0)
            connection.exec_driver_sql("INSERT INTO users (id, email) VALUES ('new', 'new@test')")
            assert connection.exec_driver_sql(
                "SELECT broker_mfa_required FROM users WHERE id='new'"
            ).scalar() == 0
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM users").scalar() == 2
        assert connection.exec_driver_sql("SELECT totp_secret_enc FROM auth_mfa").scalar() == (
            "retained-test-value"
        )
    engine.dispose()
