"""Claim submission production-readiness controls.

Revision ID: f2c3d4e5a6b7
Revises: e1b2c3d4f5a6
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f2c3d4e5a6b7"
down_revision: str | None = "e1b2c3d4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_POLICY_CHECKS = {
    "ck_policy_years_claim_grace_period_days_valid": (
        "claim_grace_period_days IS NULL OR "
        "claim_grace_period_days BETWEEN 0 AND 3650"
    ),
    "ck_policy_years_leaver_access_days_valid": (
        "leaver_access_days IS NULL OR leaver_access_days BETWEEN 0 AND 3650"
    ),
}


def _create_draft_table() -> None:
    json_type = (
        postgresql.JSONB()
        if op.get_bind().dialect.name == "postgresql"
        else sa.JSON()
    )
    op.create_table(
        "claim_form_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("employee_id", sa.String(36), nullable=False),
        sa.Column("policy_year_id", sa.String(36), nullable=False),
        sa.Column("form_data", json_type, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["policy_year_id"], ["policy_years.id"], ondelete="CASCADE"
        ),
    )
    for name, columns, unique in (
        ("ix_claim_form_drafts_client_id", ["client_id"], False),
        ("ix_claim_form_drafts_employee_id", ["employee_id"], False),
        ("ix_claim_form_drafts_policy_year_id", ["policy_year_id"], False),
        (
            "uq_claim_form_drafts_employee_year",
            ["employee_id", "policy_year_id"],
            True,
        ),
    ):
        op.create_index(name, "claim_form_drafts", columns, unique=unique)


def _add_public_columns_and_checks() -> None:
    op.add_column(
        "claim_commands", sa.Column("request_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "claim_ai_reviews",
        sa.Column("review_config_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "claim_ai_reviews",
        sa.Column(
            "review_config_snapshot",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("policy_years", recreate="always") as batch:
            for name, condition in _POLICY_CHECKS.items():
                batch.create_check_constraint(name, condition)
    else:
        for name, condition in _POLICY_CHECKS.items():
            op.create_check_constraint(name, "policy_years", condition)


def _postgres_schemas(bind: sa.engine.Connection) -> list[str]:
    schemas = ["public"]
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.claims"},
        ):
            schemas.append(schema)
    return schemas


def _assert_policy_values(bind: sa.engine.Connection, schema: str) -> None:
    invalid = bind.scalar(
        sa.text(
            f'SELECT EXISTS (SELECT 1 FROM "{schema}".policy_years '
            "WHERE claim_grace_period_days NOT BETWEEN 0 AND 3650 "
            "OR leaver_access_days NOT BETWEEN 0 AND 3650)"
        )
    )
    if invalid:
        raise RuntimeError(
            f"Cannot bound claim windows: {schema}.policy_years contains a value "
            "outside 0..3650 days."
        )


def _upgrade_firm_schema(bind: sa.engine.Connection, schema: str) -> None:
    _assert_policy_values(bind, schema)
    for table, definition in (
        ("claim_commands", "request_hash VARCHAR(64)"),
        ("claim_ai_reviews", "review_config_fingerprint VARCHAR(64)"),
        ("claim_ai_reviews", "review_config_snapshot JSONB"),
    ):
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".{table} ADD COLUMN IF NOT EXISTS {definition}'
            )
        )
    bind.execute(
        sa.text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".claim_form_drafts ('
            "id VARCHAR(36) PRIMARY KEY, "
            "client_id VARCHAR(36) NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE, "
            f'employee_id VARCHAR(36) NOT NULL REFERENCES "{schema}".employees(id) '
            "ON DELETE CASCADE, "
            f'policy_year_id VARCHAR(36) NOT NULL REFERENCES "{schema}".policy_years(id) '
            "ON DELETE CASCADE, "
            "form_data JSONB NOT NULL, version INTEGER NOT NULL DEFAULT 1, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    )
    for name, columns, unique in (
        ("ix_claim_form_drafts_client_id", "client_id", ""),
        ("ix_claim_form_drafts_employee_id", "employee_id", ""),
        ("ix_claim_form_drafts_policy_year_id", "policy_year_id", ""),
        (
            "uq_claim_form_drafts_employee_year",
            "employee_id, policy_year_id",
            "UNIQUE ",
        ),
    ):
        bind.execute(
            sa.text(
                f'CREATE {unique}INDEX IF NOT EXISTS "{name}" '
                f'ON "{schema}".claim_form_drafts ({columns})'
            )
        )
    for name, condition in _POLICY_CHECKS.items():
        bind.execute(
            sa.text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid = c.connamespace "
                f"WHERE n.nspname = '{schema}' AND c.conname = '{name}') THEN "
                f'ALTER TABLE "{schema}".policy_years ADD CONSTRAINT "{name}" '
                f"CHECK ({condition}); END IF; END $$"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        _assert_policy_values(bind, "main")
        _create_draft_table()
        _add_public_columns_and_checks()
        return
    _assert_policy_values(bind, "public")
    _create_draft_table()
    _add_public_columns_and_checks()
    for schema in _postgres_schemas(bind):
        if schema != "public":
            _upgrade_firm_schema(bind, schema)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for schema in reversed(_postgres_schemas(bind)):
            if schema == "public":
                continue
            bind.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}".claim_form_drafts'))
            for name in _POLICY_CHECKS:
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".policy_years '
                        f'DROP CONSTRAINT IF EXISTS "{name}"'
                    )
                )
            for table, column in (
                ("claim_ai_reviews", "review_config_snapshot"),
                ("claim_ai_reviews", "review_config_fingerprint"),
                ("claim_commands", "request_hash"),
            ):
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".{table} '
                        f'DROP COLUMN IF EXISTS "{column}"'
                    )
                )
        for name in _POLICY_CHECKS:
            op.drop_constraint(name, "policy_years", type_="check")
    else:
        with op.batch_alter_table("policy_years", recreate="always") as batch:
            for name in _POLICY_CHECKS:
                batch.drop_constraint(name, type_="check")
    op.drop_table("claim_form_drafts")
    op.drop_column("claim_ai_reviews", "review_config_snapshot")
    op.drop_column("claim_ai_reviews", "review_config_fingerprint")
    op.drop_column("claim_commands", "request_hash")
