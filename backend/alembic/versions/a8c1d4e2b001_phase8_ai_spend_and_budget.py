"""Phase 8 — ai_spend_log table + clients.ai_monthly_token_budget

Revision ID: a8c1d4e2b001
Revises: 3f2eb262424e
Create Date: 2026-05-12 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8c1d4e2b001"
down_revision: Union[str, None] = "3f2eb262424e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_spend_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("policy_year_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name="fk_ai_spend_log_client_id_clients", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_year_id"],
            ["policy_years.id"],
            name="fk_ai_spend_log_policy_year_id_policy_years",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_spend_log"),
    )
    op.create_index("ix_ai_spend_log_client_id", "ai_spend_log", ["client_id"])
    op.create_index("ix_ai_spend_log_policy_year_id", "ai_spend_log", ["policy_year_id"])
    op.create_index("ix_ai_spend_log_operation", "ai_spend_log", ["operation"])

    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "ai_monthly_token_budget",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("100000"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("clients", schema=None) as batch_op:
        batch_op.drop_column("ai_monthly_token_budget")

    op.drop_index("ix_ai_spend_log_operation", table_name="ai_spend_log")
    op.drop_index("ix_ai_spend_log_policy_year_id", table_name="ai_spend_log")
    op.drop_index("ix_ai_spend_log_client_id", table_name="ai_spend_log")
    op.drop_table("ai_spend_log")
