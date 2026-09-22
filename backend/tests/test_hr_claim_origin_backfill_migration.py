"""Regression coverage for legacy delegated-claim origin repair."""

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _migration():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/f5a7c9e1b3d4_backfill_hr_claim_origin.py"
    )
    spec = importlib.util.spec_from_file_location("hr_claim_origin_backfill", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_backfill_reclassifies_only_legacy_delegated_claims(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'hr-origin-backfill.db'}")
    metadata = sa.MetaData()
    claims = sa.Table(
        "claims",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("intake_meta", sa.JSON(), nullable=True),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            sa.insert(claims),
            [
                {
                    "id": "legacy-hr",
                    "origin": "portal",
                    "intake_meta": {"submission_channel": "hr"},
                },
                {
                    "id": "member",
                    "origin": "portal",
                    "intake_meta": {"submission_channel": "portal"},
                },
                {
                    "id": "already-fixed",
                    "origin": "hr",
                    "intake_meta": {"submission_channel": "hr"},
                },
            ],
        )
        connection.execute(
            sa.text(
                "INSERT INTO claims (id, origin, intake_meta) "
                "VALUES ('malformed', 'portal', :meta)"
            ),
            {"meta": json.dumps("not-an-object")},
        )
        with Operations.context(MigrationContext.configure(connection)):
            _migration().upgrade()

        rows = {
            row.id: row.origin
            for row in connection.execute(sa.text("SELECT id, origin FROM claims"))
        }
        assert rows == {
            "legacy-hr": "hr",
            "member": "portal",
            "already-fixed": "hr",
            "malformed": "portal",
        }

        with Operations.context(MigrationContext.configure(connection)):
            _migration().downgrade()
        assert connection.scalar(
            sa.text("SELECT origin FROM claims WHERE id = 'legacy-hr'")
        ) == "hr"

    engine.dispose()
