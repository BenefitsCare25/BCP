"""Frozen migration matches the domain and refuses destructive rollback."""

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import WicaIncident, WicaPeriod


def test_wica_migration_roundtrip_and_retention_guard(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/b1c3d5e7f9a2_broker_wica.py"
    spec = importlib.util.spec_from_file_location("wica_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as connection:
        for table in ("clients", "employees", "stored_documents"):
            connection.execute(sa.text(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            tables = {
                "wica_settings",
                "wica_periods",
                "wica_incidents",
                "wica_documents",
                "wica_packs",
            }
            assert tables.issubset(sa.inspect(connection).get_table_names())
            for name in tables:
                model = WicaIncident.metadata.tables[name]
                actual = {c["name"]: c for c in sa.inspect(connection).get_columns(name)}
                assert set(actual) == set(model.columns.keys())
                assert all(actual[c.name]["nullable"] == c.nullable for c in model.columns)
                for fk in sa.inspect(connection).get_foreign_keys(name):
                    expected = "SET NULL" if fk["constrained_columns"] == ["employee_id"] else None
                    assert fk["options"].get("ondelete") == expected
            migration.downgrade()
            assert tables.isdisjoint(sa.inspect(connection).get_table_names())
            migration.upgrade()
            connection.execute(sa.text("INSERT INTO clients (id) VALUES ('client')"))
            connection.execute(
                sa.insert(WicaPeriod.__table__).values(
                    id="period",
                    client_id="client",
                    label="2026",
                    start_date=date(2026, 1, 1),
                    end_date=date(2026, 12, 31),
                )
            )
            connection.execute(
                sa.insert(WicaIncident.__table__).values(
                    client_id="client",
                    period_id="period",
                    employee_name="Retained",
                    staff_id="001",
                    incident_date=date(2026, 1, 1),
                    report_number="",
                    remarks="",
                )
            )
            with pytest.raises(RuntimeError, match="retained incidents"):
                migration.downgrade()
            assert tables.issubset(sa.inspect(connection).get_table_names())
    engine.dispose()
