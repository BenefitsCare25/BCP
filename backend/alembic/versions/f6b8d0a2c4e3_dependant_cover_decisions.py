"""Dependant cover decisions: a broker's keep/exclude call on one dependant's
cover under one product (an over-age child kept for full-time study, or one
taken off the product). Additive tenant table only.

Revision ID: f6b8d0a2c4e3
Revises: d9e1f3a5c7b2
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6b8d0a2c4e3"
down_revision: str | None = "d9e1f3a5c7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "dependant_cover_decisions"


def _schemas(bind: sa.engine.Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
        if sa.inspect(bind).has_table("dependants", schema=schema):
            schemas.append(schema)
    return schemas


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        # Model-based firm provisioning may already have created the table.
        if sa.inspect(bind).has_table(_TABLE, schema=schema):
            continue
        prefix = f"{schema}." if schema else ""
        clients = "public.clients.id" if schema else "clients.id"
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id", sa.String(36),
                sa.ForeignKey(clients, ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "policy_year_id", sa.String(36),
                sa.ForeignKey(f"{prefix}policy_years.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "dependant_id", sa.String(36),
                sa.ForeignKey(f"{prefix}dependants.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "product_id", sa.String(36),
                sa.ForeignKey(f"{prefix}products.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("decision", sa.String(16), nullable=False),
            sa.Column("reason", sa.String(255)),
            sa.Column("decided_by", sa.String(36)),
            sa.Column(
                "created_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.func.now(), nullable=False,
            ),
            sa.UniqueConstraint(
                "policy_year_id", "dependant_id", "product_id",
                name="uq_dependant_cover_decision",
            ),
            schema=schema,
        )
        for column in ("client_id", "policy_year_id", "dependant_id", "product_id"):
            op.create_index(
                f"ix_{_TABLE}_{column}", _TABLE, [column], schema=schema,
            )


def downgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        if sa.inspect(bind).has_table(_TABLE, schema=schema):
            op.drop_table(_TABLE, schema=schema)
