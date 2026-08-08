"""Make a report version's number unique within its series.

`create_version` reads the series max and writes one past it, which races. That
was survivable when a version cost a deliberate "Save version" click; retention
now happens on every submission download, so two brokers pulling one insurer's
submission at the same moment can both write v7 — and `previous_version` uses a
strict `<`, so the movement diff steps over one of them and a whole submission
drops out of "what changed since last time".

The index already existed (non-unique) with the same name and columns, so this
recreates it as UNIQUE. Duplicates would make that fail, which is the correct
outcome: they must be renumbered deliberately, not silently collapsed.

Revision ID: a3f7c2d9e614
Revises: e7a2b9c4d150
"""
from __future__ import annotations

from alembic import op

revision = "a3f7c2d9e614"
down_revision = "e7a2b9c4d150"
branch_labels = None
depends_on = None

_NAME = "ix_report_versions_series"
_COLS = ["client_id", "policy_year_id", "report_type", "scope_key", "version_no"]


def upgrade() -> None:
    op.drop_index(_NAME, table_name="report_versions")
    op.create_index(_NAME, "report_versions", _COLS, unique=True)


def downgrade() -> None:
    op.drop_index(_NAME, table_name="report_versions")
    op.create_index(_NAME, "report_versions", _COLS, unique=False)
