"""Product terms carry GST config; coverage dates become optional

`product_terms` gains `gst_included` + `gst_rate` (raw slip amounts are
GST-exclusive; the toggle grosses up premium displays/computations). The
coverage dates turn nullable so a row can exist for GST alone — null dates
inherit the policy year's span, exactly like a missing row.

`gst_included` is nullable (tri-state): NULL = no product opinion (flex tags
inherit the flex-scheme default; the insurance premium is not grossed), True =
gross by `gst_rate`, False = explicit "no GST" (overrides the scheme default).

Revision ID: b4d8e2f6a1c3
Revises: c3f7a9d1e5b8
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4d8e2f6a1c3"
down_revision: Union[str, None] = "c3f7a9d1e5b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _sqlite_fk(bind, on: bool) -> None:
    """SQLite only: toggle FK enforcement around batch table-recreates.

    The app engine sets PRAGMA foreign_keys=ON per connection, and SQLite's
    DROP TABLE (which batch mode performs) would otherwise fire ON DELETE
    CASCADE into child tables. Postgres alters in place and never hits this."""
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if on else 'OFF'}")


def upgrade() -> None:
    bind = op.get_bind()
    _sqlite_fk(bind, on=False)
    with op.batch_alter_table("product_terms") as batch:
        batch.add_column(sa.Column("gst_included", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("gst_rate", sa.Float(), nullable=True))
        batch.alter_column("coverage_start", existing_type=sa.Date(), nullable=True)
        batch.alter_column("coverage_end", existing_type=sa.Date(), nullable=True)
    _sqlite_fk(bind, on=True)


def downgrade() -> None:
    bind = op.get_bind()
    # Rows that existed only for GST have no dates to re-tighten around — drop
    # them before restoring NOT NULL.
    op.execute(
        "DELETE FROM product_terms WHERE coverage_start IS NULL OR coverage_end IS NULL"
    )
    _sqlite_fk(bind, on=False)
    with op.batch_alter_table("product_terms") as batch:
        batch.alter_column("coverage_end", existing_type=sa.Date(), nullable=False)
        batch.alter_column("coverage_start", existing_type=sa.Date(), nullable=False)
        batch.drop_column("gst_rate")
        batch.drop_column("gst_included")
    _sqlite_fk(bind, on=True)
