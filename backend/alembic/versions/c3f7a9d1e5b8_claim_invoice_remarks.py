"""Add invoice_number + remarks to claims

Member-transcribed receipt/invoice number (AI cross-checks it against the
uploaded documents) and a free-text member remark. Plain ADD COLUMN — SQLite
supports it natively, so no batch_alter_table / FK toggle is needed.

Revision ID: c3f7a9d1e5b8
Revises: f9c2d4e6b8a0
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3f7a9d1e5b8"
down_revision: Union[str, None] = "f9c2d4e6b8a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("invoice_number", sa.String(128), nullable=True))
    op.add_column("claims", sa.Column("remarks", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("claims", "remarks")
    op.drop_column("claims", "invoice_number")
