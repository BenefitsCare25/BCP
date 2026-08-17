"""Durable claim review jobs and progress state.

Revision ID: f3a5c7e9b1d4
Revises: e2c4f6a8b1d3
Create Date: 2026-08-17
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a5c7e9b1d4"
down_revision: str | None = "e2c4f6a8b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claim_review_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("broker_firm_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("claim_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("state", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("last_error_detail", sa.Text()),
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
    )
    op.create_index(
        "ix_claim_review_jobs_state_available",
        "claim_review_jobs",
        ["state", "available_at"],
    )
    op.create_index(
        "ix_claim_review_jobs_state_lease",
        "claim_review_jobs",
        ["state", "lease_expires_at"],
    )
    op.create_index("ix_claim_review_jobs_broker_firm_id", "claim_review_jobs", ["broker_firm_id"])
    op.create_index("ix_claim_review_jobs_client_id", "claim_review_jobs", ["client_id"])
    op.create_index("ix_claim_review_jobs_claim_id", "claim_review_jobs", ["claim_id"])
    op.create_index("uq_claim_review_jobs_review", "claim_review_jobs", ["review_id"], unique=True)
    op.create_index(
        "uq_claim_review_jobs_active_claim",
        "claim_review_jobs",
        ["claim_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running', 'retry_wait')"),
        sqlite_where=sa.text("state IN ('queued', 'running', 'retry_wait')"),
    )

    review_columns = (
        sa.Column("error_code", sa.String(64)),
        sa.Column("stage", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "deterministic_short_circuit",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    for column in review_columns:
        op.add_column("claim_ai_reviews", column)
    op.execute("UPDATE claim_ai_reviews SET status = 'queued' WHERE status = 'pending'")
    op.execute(
        "UPDATE claim_ai_reviews SET superseded = true WHERE id IN ("
        "SELECT id FROM (SELECT id, row_number() OVER ("
        "PARTITION BY claim_id ORDER BY created_at DESC, id DESC"
        ") AS rn FROM claim_ai_reviews WHERE superseded = false) ranked "
        "WHERE rn > 1)"
    )
    op.create_index(
        "uq_claim_ai_reviews_active_claim",
        "claim_ai_reviews",
        ["claim_id"],
        unique=True,
        postgresql_where=sa.text("superseded = false"),
        sqlite_where=sa.text("superseded = 0"),
    )

    validation_columns = (
        ("validated_fingerprint", sa.String(16)),
        ("validated_model", sa.String(128)),
        ("validated_location", sa.String(64)),
        ("validated_capacity_mode", sa.String(32)),
        ("validation_status", sa.String(24)),
    )
    for table in ("client_ai_configs", "platform_ai_settings"):
        op.add_column(table, sa.Column("capacity_mode", sa.String(32), nullable=True))
        for name, kind in validation_columns:
            op.add_column(table, sa.Column(name, kind, nullable=True))

    _upgrade_tenant_review_tables()


def _upgrade_tenant_review_tables() -> None:
    """Apply the data/index migration to every real firm schema."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        exists = bind.scalar(
            sa.text(
                "SELECT to_regclass(:table_name) IS NOT NULL"
            ),
            {"table_name": f'{schema}.claim_ai_reviews'},
        )
        if not exists:
            continue
        columns = (
            'ADD COLUMN IF NOT EXISTS error_code VARCHAR(64)',
            "ADD COLUMN IF NOT EXISTS stage VARCHAR(24) NOT NULL DEFAULT 'queued'",
            'ADD COLUMN IF NOT EXISTS progress_current INTEGER NOT NULL DEFAULT 0',
            'ADD COLUMN IF NOT EXISTS progress_total INTEGER NOT NULL DEFAULT 0',
            'ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 0',
            'ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ',
            'ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ',
            'ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ',
            'ADD COLUMN IF NOT EXISTS deterministic_short_circuit BOOLEAN NOT NULL DEFAULT false',
        )
        for definition in columns:
            bind.execute(sa.text(f'ALTER TABLE "{schema}".claim_ai_reviews {definition}'))
        bind.execute(
            sa.text(
                f"UPDATE \"{schema}\".claim_ai_reviews "
                "SET status='queued' WHERE status='pending'"
            )
        )
        bind.execute(
            sa.text(
                f'WITH ranked AS (SELECT id, row_number() OVER '
                f'(PARTITION BY claim_id ORDER BY created_at DESC, id DESC) AS rn '
                f'FROM "{schema}".claim_ai_reviews WHERE superseded=false) '
                f'UPDATE "{schema}".claim_ai_reviews r SET superseded=true '
                f'FROM ranked WHERE r.id=ranked.id AND ranked.rn>1'
            )
        )
        bind.execute(
            sa.text(
                f'CREATE UNIQUE INDEX IF NOT EXISTS uq_claim_ai_reviews_active_claim '
                f'ON "{schema}".claim_ai_reviews (claim_id) WHERE superseded=false'
            )
        )


def downgrade() -> None:
    _downgrade_tenant_review_tables()
    for table in ("platform_ai_settings", "client_ai_configs"):
        for name in (
            "validation_status", "validated_capacity_mode", "validated_location",
            "validated_model", "validated_fingerprint",
        ):
            op.drop_column(table, name)
        op.drop_column(table, "capacity_mode")
    op.drop_index("uq_claim_ai_reviews_active_claim", table_name="claim_ai_reviews")
    for name in (
        "deterministic_short_circuit", "completed_at", "heartbeat_at", "started_at",
        "attempt", "progress_total", "progress_current", "stage", "error_code",
    ):
        op.drop_column("claim_ai_reviews", name)
    op.drop_table("claim_review_jobs")


def _downgrade_tenant_review_tables() -> None:
    """Remove additive review fields from every real firm schema."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    for firm_id in firm_ids:
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        exists = bind.scalar(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.claim_ai_reviews"},
        )
        if not exists:
            continue
        bind.execute(
            sa.text(
                f'DROP INDEX IF EXISTS "{schema}".uq_claim_ai_reviews_active_claim'
            )
        )
        for column in (
            "deterministic_short_circuit",
            "completed_at",
            "heartbeat_at",
            "started_at",
            "attempt",
            "progress_total",
            "progress_current",
            "stage",
            "error_code",
        ):
            bind.execute(
                sa.text(
                    f'ALTER TABLE "{schema}".claim_ai_reviews '
                    f'DROP COLUMN IF EXISTS "{column}"'
                )
            )
