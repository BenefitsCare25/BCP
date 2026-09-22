"""Regression coverage for the delegated-HR claim-origin migration."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _table_sql(connection: sa.Connection) -> str:
    return str(
        connection.scalar(
            sa.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'claims'"
            )
        )
    )


def test_hr_origin_migration_round_trip_keeps_delegated_claims_internal(tmp_path: Path) -> None:
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/e4f6a8c0b2d3_hr_claim_origin.py"
    )
    spec = importlib.util.spec_from_file_location("hr_claim_origin_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    metadata = sa.MetaData()
    claims = sa.Table(
        "claims",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "origin IN ('portal', 'broker')",
            name="ck_claims_origin_valid",
        ),
    )
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'hr-origin.db'}")
    metadata.create_all(engine)

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(sa.insert(claims).values(id="delegated", origin="hr"))
        assert "'hr'" in _table_sql(connection)

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert connection.scalar(
            sa.text("SELECT origin FROM claims WHERE id = 'delegated'")
        ) == "broker"
        assert "'hr'" not in _table_sql(connection)

    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(sa.text("INSERT INTO claims VALUES ('delegated-2', 'hr')"))
        assert connection.scalar(
            sa.text("SELECT origin FROM claims WHERE id = 'delegated-2'")
        ) == "hr"

    engine.dispose()
