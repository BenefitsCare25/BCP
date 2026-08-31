"""add roster mapping profiles and attribute capability flags

Revision ID: f9a1c3e5b7d2
Revises: e8c0d2f4b6a9
Create Date: 2026-08-31

Expand-only DDL. Existing attribute and eligibility identifiers are preserved.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant

revision: str = "f9a1c3e5b7d2"
down_revision: str | None = "e8c0d2f4b6a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _firm_schemas(bind: sa.engine.Connection) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    return [
        "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
        for firm_id in bind.execute(
            sa.text("SELECT id FROM public.broker_firms")
        ).scalars()
    ]


def _profile_columns() -> list[sa.Column | sa.Constraint]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("member_type", sa.String(24), nullable=False, server_default="employee"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("sheet_name", sa.String(255), nullable=True),
        sa.Column("source_headers", json_variant(), nullable=False),
        sa.Column("column_mapping", json_variant(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "client_id", "member_type", "fingerprint",
            name="uq_roster_mapping_profile_client_type_fingerprint",
        ),
    ]


def upgrade() -> None:
    op.add_column(
        "employee_attribute_schemas",
        sa.Column("allow_matching", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "employee_attribute_schemas",
        sa.Column("allow_ai_values", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table("roster_mapping_profiles", *_profile_columns())
    op.create_index(
        "ix_roster_mapping_profiles_client_id", "roster_mapping_profiles", ["client_id"]
    )
    op.create_index(
        "ix_roster_mapping_profiles_fingerprint", "roster_mapping_profiles", ["fingerprint"]
    )

    bind = op.get_bind()
    for schema in _firm_schemas(bind):
        if not bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.employee_attribute_schemas"},
        ):
            continue
        bind.execute(sa.text(
            f'ALTER TABLE "{schema}".employee_attribute_schemas '
            "ADD COLUMN IF NOT EXISTS allow_matching BOOLEAN NOT NULL DEFAULT TRUE, "
            "ADD COLUMN IF NOT EXISTS allow_ai_values BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        bind.execute(sa.text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".roster_mapping_profiles ('
            "id VARCHAR(36) PRIMARY KEY, client_id VARCHAR(36) NOT NULL "
            "REFERENCES public.clients(id) ON DELETE CASCADE, "
            "member_type VARCHAR(24) NOT NULL DEFAULT 'employee', "
            "fingerprint VARCHAR(64) NOT NULL, sheet_name VARCHAR(255), "
            "source_headers JSONB NOT NULL, column_mapping JSONB NOT NULL, "
            "created_by VARCHAR(36), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "CONSTRAINT uq_roster_mapping_profile_client_type_fingerprint "
            "UNIQUE (client_id, member_type, fingerprint))"
        ))
        bind.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS ix_roster_mapping_profiles_client_id '
            f'ON "{schema}".roster_mapping_profiles (client_id)'
        ))
        bind.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS ix_roster_mapping_profiles_fingerprint '
            f'ON "{schema}".roster_mapping_profiles (fingerprint)'
        ))


def downgrade() -> None:
    bind = op.get_bind()
    for schema in _firm_schemas(bind):
        bind.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}".roster_mapping_profiles'))
        bind.execute(sa.text(
            f'ALTER TABLE "{schema}".employee_attribute_schemas '
            "DROP COLUMN IF EXISTS allow_ai_values, DROP COLUMN IF EXISTS allow_matching"
        ))
    op.drop_index("ix_roster_mapping_profiles_fingerprint", table_name="roster_mapping_profiles")
    op.drop_index("ix_roster_mapping_profiles_client_id", table_name="roster_mapping_profiles")
    op.drop_table("roster_mapping_profiles")
    op.drop_column("employee_attribute_schemas", "allow_ai_values")
    op.drop_column("employee_attribute_schemas", "allow_matching")
