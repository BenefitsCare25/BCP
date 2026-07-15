"""Panel listings become a shared library

`panel_listings.client_id` turns nullable: NULL = library entry uploaded once
and selectable by every company (the `tenant_or_global` pattern, like
`products`). Existing rows are converted to library entries — per-company
applicability is already expressed by `policy_year_panels` tags, which are
untouched.

Revision ID: f9c2d4e6b8a0
Revises: e8b1c3d5a7f9
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9c2d4e6b8a0"
down_revision: Union[str, None] = "e8b1c3d5a7f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sqlite_fk(bind, on: bool) -> None:
    """SQLite only: toggle FK enforcement around batch table-recreates.

    The app engine sets PRAGMA foreign_keys=ON per connection, and SQLite's
    DROP TABLE (which batch mode performs) then fires ON DELETE CASCADE into
    panel_clinics / policy_year_panels — silently wiping them. Postgres does
    an in-place ALTER COLUMN and never hits this."""
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")


def upgrade() -> None:
    bind = op.get_bind()
    _sqlite_fk(bind, on=False)
    # batch mode: SQLite can't ALTER COLUMN in place (table is recreated).
    with op.batch_alter_table("panel_listings") as batch:
        batch.alter_column(
            "client_id", existing_type=sa.String(36), nullable=True
        )
    op.execute("UPDATE panel_listings SET client_id = NULL")
    _sqlite_fk(bind, on=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Library entries have no owning client to restore — remove them (their
    # clinics + policy-year tags cascade), then re-tighten the column.
    op.execute("DELETE FROM panel_listings WHERE client_id IS NULL")
    _sqlite_fk(bind, on=False)
    with op.batch_alter_table("panel_listings") as batch:
        batch.alter_column(
            "client_id", existing_type=sa.String(36), nullable=False
        )
    _sqlite_fk(bind, on=True)
