"""Company-scoped eligibility mapping profiles and category validation state.

Revision ID: d7f9b1c3e5a8
Revises: c6e8a0b2d4f7
Create Date: 2026-08-22

This is an additive expand migration: the new category columns are nullable and
existing matching rules continue to be read exactly as before. No data backfill
or table rewrite is required on PostgreSQL.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant

revision = "d7f9b1c3e5a8"
down_revision = "c6e8a0b2d4f7"
branch_labels = None
depends_on = None


def _profile_columns() -> list[sa.Column | sa.Constraint]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("category_signature", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(512), nullable=False),
        sa.Column("matching_rule", json_variant(), nullable=True),
        sa.Column("rule_human_readable", sa.String(1024), nullable=True),
        sa.Column("required_attributes", json_variant(), nullable=True),
        sa.Column("validation", json_variant(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("last_policy_year_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "client_id",
            "category_signature",
            name="uq_eligibility_mapping_profile_client_signature",
        ),
    ]


def _postgres_firm_schemas(bind) -> list[str]:
    if bind.dialect.name != "postgresql":
        return []
    return [
        "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        for firm_id in bind.execute(
            sa.text("SELECT id FROM public.broker_firms")
        ).scalars()
    ]


def upgrade() -> None:
    op.create_table("eligibility_mapping_profiles", *_profile_columns())
    op.create_index(
        "ix_eligibility_mapping_profiles_client_id",
        "eligibility_mapping_profiles",
        ["client_id"],
    )
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot ALTER a table to add a foreign-key constraint. Batch
        # mode performs Alembic's copy-and-move rebuild and keeps local/test
        # databases equivalent to PostgreSQL.
        with op.batch_alter_table("categories") as batch_op:
            batch_op.add_column(
                sa.Column("mapping_profile_id", sa.String(36), nullable=True)
            )
            batch_op.add_column(
                sa.Column("rule_status", sa.String(32), nullable=True)
            )
            batch_op.add_column(
                sa.Column("rule_validation", json_variant(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_categories_mapping_profile_id_eligibility_mapping_profiles",
                "eligibility_mapping_profiles",
                ["mapping_profile_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                "ix_categories_mapping_profile_id", ["mapping_profile_id"]
            )
            batch_op.create_index("ix_categories_rule_status", ["rule_status"])
    else:
        op.add_column(
            "categories",
            sa.Column(
                "mapping_profile_id",
                sa.String(36),
                sa.ForeignKey(
                    "eligibility_mapping_profiles.id",
                    name=(
                        "fk_categories_mapping_profile_id_"
                        "eligibility_mapping_profiles"
                    ),
                    ondelete="SET NULL",
                ),
                nullable=True,
            ),
        )
        op.add_column(
            "categories", sa.Column("rule_status", sa.String(32), nullable=True)
        )
        op.add_column(
            "categories", sa.Column("rule_validation", json_variant(), nullable=True)
        )
        op.create_index(
            "ix_categories_mapping_profile_id", "categories", ["mapping_profile_id"]
        )
        op.create_index("ix_categories_rule_status", "categories", ["rule_status"])

    for schema in _postgres_firm_schemas(bind):
        if not bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.categories"},
        ):
            continue
        bind.execute(
            sa.text(
                f'CREATE TABLE IF NOT EXISTS "{schema}".eligibility_mapping_profiles ('
                "id VARCHAR(36) PRIMARY KEY, "
                "client_id VARCHAR(36) NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE, "
                "category_signature VARCHAR(512) NOT NULL, "
                "display_name VARCHAR(512) NOT NULL, matching_rule JSONB, "
                "rule_human_readable VARCHAR(1024), required_attributes JSONB, "
                "validation JSONB, source VARCHAR(32) NOT NULL DEFAULT 'manual', "
                "confidence DOUBLE PRECISION, status VARCHAR(32) NOT NULL DEFAULT 'proposed', "
                "last_policy_year_id VARCHAR(36), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "CONSTRAINT uq_eligibility_mapping_profile_client_signature "
                "UNIQUE (client_id, category_signature))"
            )
        )
        bind.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_eligibility_mapping_profiles_client_id '
                f'ON "{schema}".eligibility_mapping_profiles (client_id)'
            )
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".categories ADD COLUMN IF NOT EXISTS '
                f'mapping_profile_id VARCHAR(36) REFERENCES '
                f'"{schema}".eligibility_mapping_profiles(id) ON DELETE SET NULL'
            )
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".categories ADD COLUMN IF NOT EXISTS '
                "rule_status VARCHAR(32)"
            )
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".categories ADD COLUMN IF NOT EXISTS '
                "rule_validation JSONB"
            )
        )
        bind.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_categories_mapping_profile_id '
                f'ON "{schema}".categories (mapping_profile_id)'
            )
        )
        bind.execute(
            sa.text(
                f'CREATE INDEX IF NOT EXISTS ix_categories_rule_status '
                f'ON "{schema}".categories (rule_status)'
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for schema in _postgres_firm_schemas(bind):
        bind.execute(
            sa.text(f'DROP INDEX IF EXISTS "{schema}".ix_categories_rule_status')
        )
        bind.execute(
            sa.text(f'DROP INDEX IF EXISTS "{schema}".ix_categories_mapping_profile_id')
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".categories DROP COLUMN IF EXISTS rule_validation'
            )
        )
        bind.execute(
            sa.text(f'ALTER TABLE "{schema}".categories DROP COLUMN IF EXISTS rule_status')
        )
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".categories DROP COLUMN IF EXISTS mapping_profile_id'
            )
        )
        bind.execute(
            sa.text(f'DROP TABLE IF EXISTS "{schema}".eligibility_mapping_profiles')
        )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("categories") as batch_op:
            batch_op.drop_index("ix_categories_rule_status")
            batch_op.drop_index("ix_categories_mapping_profile_id")
            batch_op.drop_constraint(
                "fk_categories_mapping_profile_id_eligibility_mapping_profiles",
                type_="foreignkey",
            )
            batch_op.drop_column("rule_validation")
            batch_op.drop_column("rule_status")
            batch_op.drop_column("mapping_profile_id")
    else:
        op.drop_index("ix_categories_rule_status", table_name="categories")
        op.drop_index("ix_categories_mapping_profile_id", table_name="categories")
        op.drop_column("categories", "rule_validation")
        op.drop_column("categories", "rule_status")
        op.drop_column("categories", "mapping_profile_id")
    op.drop_index(
        "ix_eligibility_mapping_profiles_client_id",
        table_name="eligibility_mapping_profiles",
    )
    op.drop_table("eligibility_mapping_profiles")
