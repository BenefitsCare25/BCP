"""category source as string + add system_generated

Revision ID: 3f2eb262424e
Revises: 1a54b8d5880e
Create Date: 2026-05-12 15:19:35.234618

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3f2eb262424e'
down_revision: Union[str, None] = '1a54b8d5880e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Convert `categories.source` and `categories.status` from native Enum
    columns to plain VARCHAR(32) so new source/status values don't require a
    schema migration.

    On Postgres the original columns are ENUM types (`sourcekind`,
    `categorystatus`) created by `1a54b8d5880e`; ALTER TYPE requires a USING
    clause and the now-orphan enum types must be dropped explicitly. On
    SQLite the columns were VARCHAR all along, so a batch rebuild suffices.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE categories "
            "ALTER COLUMN source TYPE VARCHAR(32) USING source::text"
        )
        op.execute(
            "ALTER TABLE categories "
            "ALTER COLUMN status TYPE VARCHAR(32) USING status::text"
        )
        # The enum types are no longer referenced by any column — drop them
        # to avoid orphan rows in pg_type. CASCADE protects against any
        # surprise dependencies.
        op.execute("DROP TYPE IF EXISTS sourcekind CASCADE")
        op.execute("DROP TYPE IF EXISTS categorystatus CASCADE")
    else:
        with op.batch_alter_table("categories", schema=None) as batch_op:
            batch_op.alter_column(
                "source",
                existing_type=sa.VARCHAR(length=12),
                type_=sa.String(length=32),
                existing_nullable=False,
            )
            batch_op.alter_column(
                "status",
                existing_type=sa.VARCHAR(length=12),
                type_=sa.String(length=32),
                existing_nullable=False,
            )


def downgrade() -> None:
    """Refuse to downgrade if any source/status row holds a value longer
    than the original VARCHAR(12) — silently truncating `system_generated`
    (16 chars) corrupts the audit trail.
    """
    bind = op.get_bind()
    result = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM categories "
        "WHERE LENGTH(source) > 12 OR LENGTH(status) > 12"
    ).scalar()
    if (result or 0) > 0:
        raise RuntimeError(
            f"Cannot downgrade: {result} category rows have source/status "
            "values longer than the legacy VARCHAR(12). Truncating them "
            "would corrupt the provenance envelope. Migrate data first."
        )

    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=32),
            type_=sa.VARCHAR(length=12),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=32),
            type_=sa.VARCHAR(length=12),
            existing_nullable=False,
        )
