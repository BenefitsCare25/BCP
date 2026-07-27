"""Platform-wide Vertex credentials on platform_ai_settings

The platform key is the DEFAULT every company runs on; per-company BYOK
(`client_ai_configs`) stays an optional override. Columns mirror
`client_ai_configs` so one editor + one test path serves both.

Revision ID: c7e9a1b3d5f6
Revises: a3c5e7b9d1f4
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e9a1b3d5f6"
down_revision: Union[str, None] = "a3c5e7b9d1f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# platform_ai_settings is a CONTROL table — it lives in `public` on Postgres
# and is never copied into firm schemas, so no per-schema loop here.
_TABLE = "platform_ai_settings"
_COLUMN_NAMES = (
    "provider",
    "location",
    "model",
    "encrypted_service_account",
    "key_fingerprint",
    "last_validated_at",
    "last_validation_error",
)


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("provider", sa.String(32), nullable=True))
    op.add_column(_TABLE, sa.Column("location", sa.String(64), nullable=True))
    op.add_column(_TABLE, sa.Column("model", sa.String(128), nullable=True))
    op.add_column(
        _TABLE, sa.Column("encrypted_service_account", sa.LargeBinary(), nullable=True)
    )
    op.add_column(_TABLE, sa.Column("key_fingerprint", sa.String(16), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _TABLE, sa.Column("last_validation_error", sa.String(512), nullable=True)
    )


def downgrade() -> None:
    for name in reversed(_COLUMN_NAMES):
        op.drop_column(_TABLE, name)
