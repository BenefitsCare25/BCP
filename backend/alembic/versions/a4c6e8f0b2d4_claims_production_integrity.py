"""Claims production integrity controls.

Revision ID: a4c6e8f0b2d4
Revises: f3a5c7e9b1d4
Create Date: 2026-08-17
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4c6e8f0b2d4"
down_revision: str | None = "f3a5c7e9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY_COLUMNS = {
    "amount_claimed": "NUMERIC(18, 2)",
    "amount_converted": "NUMERIC(18, 2)",
    "fx_rate": "NUMERIC(18, 8)",
    "amount_approved": "NUMERIC(18, 2)",
    "payment_amount": "NUMERIC(18, 2)",
}

_CLAIM_CHECKS = {
    "ck_claims_claim_kind_valid": "claim_kind IN ('insured', 'flex')",
    "ck_claims_case_type_valid": "case_type IN ('claim', 'log')",
    "ck_claims_origin_valid": "origin IN ('portal', 'broker')",
    "ck_claims_status_valid": (
        "status IN ('draft', 'submitted', 'ai_review_pending', 'ai_verified', "
        "'ai_flagged', 'needs_info', 'approved', 'rejected', "
        "'sent_to_insurer', 'paid')"
    ),
    "ck_claims_amount_claimed_valid": (
        "amount_claimed > 0 AND amount_claimed <= 1000000"
    ),
    "ck_claims_amount_converted_valid": (
        "amount_converted IS NULL OR "
        "(amount_converted > 0 AND amount_converted <= 1000000)"
    ),
    "ck_claims_amount_approved_valid": (
        "amount_approved IS NULL OR "
        "(amount_approved > 0 AND amount_approved <= 1000000)"
    ),
    "ck_claims_fx_rate_valid": (
        "fx_rate IS NULL OR (fx_rate > 0 AND fx_rate <= 1000000)"
    ),
    "ck_claims_payment_amount_valid": (
        "payment_amount IS NULL OR "
        "(payment_amount >= 0 AND payment_amount <= 1000000)"
    ),
}


def _create_claim_commands() -> None:
    op.create_table(
        "claim_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_claim_commands_client_id", "claim_commands", ["client_id"])
    op.create_index("ix_claim_commands_claim_id", "claim_commands", ["claim_id"])
    op.create_index(
        "uq_claim_commands_client_key",
        "claim_commands",
        ["client_id", "idempotency_key"],
        unique=True,
    )


def _create_claim_notifications() -> None:
    op.create_table(
        "claim_notifications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("source_message_id", sa.String(36), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'dead')",
            name="ck_claim_notifications_status_valid",
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_claim_notifications_client_id", "claim_notifications", ["client_id"]
    )
    op.create_index(
        "ix_claim_notifications_claim_id", "claim_notifications", ["claim_id"]
    )
    op.create_index(
        "ix_claim_notifications_delivery",
        "claim_notifications",
        ["status", "available_at"],
    )
    op.create_index(
        "uq_claim_notifications_source_message",
        "claim_notifications",
        ["source_message_id"],
        unique=True,
    )


def _add_shared_columns() -> None:
    for name, kind in (
        ("request_id", sa.String(200)),
        ("ip_address", sa.String(64)),
        ("user_agent", sa.String(512)),
    ):
        op.add_column("audit_log", sa.Column(name, kind, nullable=True))
    op.create_index("ix_audit_log_request_id", "audit_log", ["request_id"])
    columns = (
        sa.Column(
            "storage_state",
            sa.String(24),
            nullable=False,
            server_default="available",
        ),
        sa.Column("delete_error", sa.String(255), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("stored_documents", recreate="always") as batch:
            for column in columns:
                batch.add_column(column)
            batch.create_check_constraint(
                "ck_stored_documents_storage_state_valid",
                "storage_state IN ('available', 'delete_pending')",
            )
    else:
        for column in columns:
            op.add_column("stored_documents", column)
        op.create_check_constraint(
            "ck_stored_documents_storage_state_valid",
            "stored_documents",
            "storage_state IN ('available', 'delete_pending')",
        )


def _upgrade_sqlite_claims() -> None:
    with op.batch_alter_table("claims", recreate="always") as batch:
        for column in _MONEY_COLUMNS:
            scale = 8 if column == "fx_rate" else 2
            batch.alter_column(
                column,
                existing_type=sa.Float(),
                type_=sa.Numeric(18, scale),
            )
        for name, condition in _CLAIM_CHECKS.items():
            batch.create_check_constraint(name, condition)


def _constraint_sql(schema: str, table: str, name: str, condition: str) -> str:
    return (
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid=c.connamespace "
        f"WHERE n.nspname='{schema}' AND c.conname='{name}') THEN "
        f'ALTER TABLE "{schema}".{table} ADD CONSTRAINT "{name}" '
        f"CHECK ({condition}) NOT VALID; "
        "END IF; END $$"
    )


def _upgrade_postgres_schema(bind, schema: str) -> None:
    for column, sql_type in _MONEY_COLUMNS.items():
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".claims ALTER COLUMN "{column}" '
                f"TYPE {sql_type} USING round(\"{column}\"::numeric, "
                f"{8 if column == 'fx_rate' else 2})"
            )
        )
    for name, condition in _CLAIM_CHECKS.items():
        bind.execute(sa.text(_constraint_sql(schema, "claims", name, condition)))
        bind.execute(
            sa.text(
                f'ALTER TABLE "{schema}".claims VALIDATE CONSTRAINT "{name}"'
            )
        )
    for definition in (
        'ADD COLUMN IF NOT EXISTS request_id VARCHAR(200)',
        'ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64)',
        'ADD COLUMN IF NOT EXISTS user_agent VARCHAR(512)',
    ):
        bind.execute(sa.text(f'ALTER TABLE "{schema}".audit_log {definition}'))
    bind.execute(
        sa.text(
            f'CREATE INDEX IF NOT EXISTS ix_audit_log_request_id '
            f'ON "{schema}".audit_log (request_id)'
        )
    )
    for definition in (
        "ADD COLUMN IF NOT EXISTS storage_state VARCHAR(24) "
        "NOT NULL DEFAULT 'available'",
        "ADD COLUMN IF NOT EXISTS delete_error VARCHAR(255)",
    ):
        bind.execute(sa.text(f'ALTER TABLE "{schema}".stored_documents {definition}'))
    bind.execute(
        sa.text(
            _constraint_sql(
                schema,
                "stored_documents",
                "ck_stored_documents_storage_state_valid",
                "storage_state IN ('available', 'delete_pending')",
            )
        )
    )
    bind.execute(
        sa.text(
            f'ALTER TABLE "{schema}".stored_documents VALIDATE CONSTRAINT '
            '"ck_stored_documents_storage_state_valid"'
        )
    )
    if schema != "public":
        bind.execute(
            sa.text(
                f'CREATE TABLE IF NOT EXISTS "{schema}".claim_commands '
                '(id VARCHAR(36) PRIMARY KEY, '
                'client_id VARCHAR(36) NOT NULL REFERENCES public.clients(id) '
                'ON DELETE CASCADE, claim_id VARCHAR(36) NOT NULL, '
                'action VARCHAR(64) NOT NULL, idempotency_key VARCHAR(128) NOT NULL, '
                'created_at TIMESTAMPTZ NOT NULL DEFAULT now(), '
                'updated_at TIMESTAMPTZ NOT NULL DEFAULT now())'
            )
        )
        bind.execute(
            sa.text(
                f'CREATE TABLE IF NOT EXISTS "{schema}".claim_notifications '
                '(id VARCHAR(36) PRIMARY KEY, '
                'client_id VARCHAR(36) NOT NULL REFERENCES public.clients(id) '
                'ON DELETE CASCADE, claim_id VARCHAR(36) NOT NULL, '
                'source_message_id VARCHAR(36) NOT NULL, '
                'recipient_email VARCHAR(320) NOT NULL, status VARCHAR(16) NOT NULL '
                "CHECK (status IN ('queued', 'sending', 'sent', 'dead')), "
                'attempts INTEGER NOT NULL, available_at TIMESTAMPTZ NOT NULL, '
                'lease_expires_at TIMESTAMPTZ, sent_at TIMESTAMPTZ, '
                'last_error VARCHAR(255), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), '
                'updated_at TIMESTAMPTZ NOT NULL DEFAULT now())'
            )
        )
    bind.execute(
        sa.text(
            f'CREATE INDEX IF NOT EXISTS ix_claim_commands_client_id '
            f'ON "{schema}".claim_commands (client_id)'
        )
    )
    bind.execute(
        sa.text(
            f'CREATE INDEX IF NOT EXISTS ix_claim_commands_claim_id '
            f'ON "{schema}".claim_commands (claim_id)'
        )
    )
    bind.execute(
        sa.text(
            f'CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_commands_client_key '
            f'ON "{schema}".claim_commands (client_id, idempotency_key)'
        )
    )
    for name, columns, unique in (
        ("ix_claim_notifications_client_id", "client_id", ""),
        ("ix_claim_notifications_claim_id", "claim_id", ""),
        ("ix_claim_notifications_delivery", "status, available_at", ""),
        ("uq_claim_notifications_source_message", "source_message_id", "UNIQUE "),
    ):
        bind.execute(
            sa.text(
                f'CREATE {unique}INDEX IF NOT EXISTS {name} '
                f'ON "{schema}".claim_notifications ({columns})'
            )
        )
    bind.execute(
        sa.text(
            f'DROP TRIGGER IF EXISTS audit_log_append_only ON "{schema}".audit_log'
        )
    )
    bind.execute(
        sa.text(
            f'CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE '
            f'ON "{schema}".audit_log FOR EACH ROW '
            "EXECUTE FUNCTION public.inspro_prevent_audit_mutation()"
        )
    )


def upgrade() -> None:
    _create_claim_commands()
    _create_claim_notifications()
    _add_shared_columns()
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite_claims()
        return
    bind.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION public.inspro_prevent_audit_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'audit_log is append-only'; END; $$"
        )
    )
    _upgrade_postgres_schema(bind, "public")
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        exists = bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.claims"},
        )
        if exists:
            _upgrade_postgres_schema(bind, schema)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
        for firm_id in firm_ids:
            schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
            bind.execute(
                sa.text(
                    f'DROP TRIGGER IF EXISTS audit_log_append_only '
                    f'ON "{schema}".audit_log'
                )
            )
            bind.execute(sa.text(f'DROP TABLE IF EXISTS "{schema}".claim_commands'))
            bind.execute(
                sa.text(f'DROP TABLE IF EXISTS "{schema}".claim_notifications')
            )
            for name in _CLAIM_CHECKS:
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".claims DROP CONSTRAINT IF EXISTS '
                        f'"{name}"'
                    )
                )
            bind.execute(
                sa.text(
                    f'ALTER TABLE "{schema}".stored_documents DROP CONSTRAINT '
                    'IF EXISTS "ck_stored_documents_storage_state_valid"'
                )
            )
            for column in ("delete_error", "storage_state"):
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".stored_documents '
                        f'DROP COLUMN IF EXISTS "{column}"'
                    )
                )
            bind.execute(
                sa.text(f'DROP INDEX IF EXISTS "{schema}".ix_audit_log_request_id')
            )
            for column in ("user_agent", "ip_address", "request_id"):
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".audit_log '
                        f'DROP COLUMN IF EXISTS "{column}"'
                    )
                )
            for column in _MONEY_COLUMNS:
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "{schema}".claims ALTER COLUMN "{column}" '
                        f'TYPE DOUBLE PRECISION USING "{column}"::double precision'
                    )
                )
        bind.execute(
            sa.text('DROP TRIGGER IF EXISTS audit_log_append_only ON public.audit_log')
        )
        bind.execute(
            sa.text("DROP FUNCTION IF EXISTS public.inspro_prevent_audit_mutation()")
        )
        for name in _CLAIM_CHECKS:
            bind.execute(
                sa.text(
                    f'ALTER TABLE public.claims DROP CONSTRAINT IF EXISTS "{name}"'
                )
            )
        for column in _MONEY_COLUMNS:
            bind.execute(
                sa.text(
                    f'ALTER TABLE public.claims ALTER COLUMN "{column}" '
                    f'TYPE DOUBLE PRECISION USING "{column}"::double precision'
                )
            )
    else:
        with op.batch_alter_table("claims", recreate="always") as batch:
            for name in _CLAIM_CHECKS:
                batch.drop_constraint(name, type_="check")
            for column in _MONEY_COLUMNS:
                scale = 8 if column == "fx_rate" else 2
                batch.alter_column(
                    column,
                    existing_type=sa.Numeric(18, scale),
                    type_=sa.Float(),
                )
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("stored_documents", recreate="always") as batch:
            batch.drop_constraint(
                "ck_stored_documents_storage_state_valid", type_="check"
            )
            batch.drop_column("delete_error")
            batch.drop_column("storage_state")
    else:
        op.drop_constraint(
            "ck_stored_documents_storage_state_valid",
            "stored_documents",
            type_="check",
        )
        op.drop_column("stored_documents", "delete_error")
        op.drop_column("stored_documents", "storage_state")
    op.drop_index("ix_audit_log_request_id", table_name="audit_log")
    for column in ("user_agent", "ip_address", "request_id"):
        op.drop_column("audit_log", column)
    op.drop_table("claim_notifications")
    op.drop_table("claim_commands")
