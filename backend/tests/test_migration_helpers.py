"""Cross-dialect migration safety helpers."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.db.migration_helpers import sqlite_migration_guard


def test_sqlite_migration_guard_prevents_batch_recreate_cascades() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, "
            "FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE CASCADE)"
        )
        connection.exec_driver_sql("INSERT INTO parent (id) VALUES (1)")
        connection.exec_driver_sql("INSERT INTO child (id, parent_id) VALUES (1, 1)")
        connection.commit()

        with sqlite_migration_guard(connection):
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 0
            connection.exec_driver_sql("CREATE TABLE parent_new (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO parent_new SELECT * FROM parent")
            connection.exec_driver_sql("DROP TABLE parent")
            connection.exec_driver_sql("ALTER TABLE parent_new RENAME TO parent")

        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT count(*) FROM child").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()


def test_sqlite_migration_guard_rolls_back_integrity_failure() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL, "
            "FOREIGN KEY(parent_id) REFERENCES parent(id))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version (version_num TEXT NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO parent (id) VALUES (1)")
        connection.exec_driver_sql(
            "INSERT INTO alembic_version (version_num) VALUES ('before')"
        )
        connection.commit()

        with pytest.raises(RuntimeError, match="foreign-key violations"):
            with sqlite_migration_guard(connection):
                connection.exec_driver_sql(
                    "INSERT INTO child (id, parent_id) VALUES (1, 999)"
                )
                connection.exec_driver_sql(
                    "UPDATE alembic_version SET version_num = 'after'"
                )

        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT count(*) FROM child").scalar_one() == 0
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == "before"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    engine.dispose()
