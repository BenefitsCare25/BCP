"""Policy migration supports both normal upgrades and model-based provisioning."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import AIPolicyVersion


@pytest.mark.parametrize("provisioned", [False, True])
def test_policy_migration_preserves_provisioned_records(tmp_path, provisioned):
    path = Path(__file__).parents[1] / "alembic/versions/d8f2a4b6c0e1_ai_policy_versions.py"
    spec = importlib.util.spec_from_file_location("policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    table = AIPolicyVersion.__table__
    record = {
        "id": "saved-version", "policy_id": "policy", "version": 1,
        "title": "Retained policy", "category": "AI usage policy", "status": "published",
        "file_name": "policy.pdf", "storage_path": "platform/ai-policies/policy/version.pdf",
        "sha256": "a" * 64, "size_bytes": 100, "uploaded_by": "Administrator",
        "uploaded_by_id": "admin",
    }
    with engine.begin() as connection:
        if provisioned:
            table.create(connection)
            connection.execute(table.insert().values(**record))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            if not provisioned:
                connection.execute(table.insert().values(**record))
            migration.upgrade()
        assert connection.scalar(sa.select(table.c.title)) == "Retained policy"
        # The guard must retain the invariant that only one version is current.
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(table.insert().values(**{**record, "id": "second", "version": 2}))
    engine.dispose()
