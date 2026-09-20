"""Durable operational workflow notices in every firm schema."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from alembic import op

revision = "d3e5f7a9b1c2"
down_revision = "c2d4e6f8a0b1"
branch_labels = depends_on = None


def _schemas(bind: Connection) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    schemas = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.employees"},
        ):
            schemas.append(schema)
    return schemas


def _has_table(bind: Connection, table: str, schema: str | None) -> bool:
    return sa.inspect(bind).has_table(table, schema=schema)


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        if _has_table(bind, "workflow_notifications", schema):
            continue
        client = "public.clients.id" if schema else "clients.id"
        op.create_table(
            "workflow_notifications",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id",
                sa.String(36),
                sa.ForeignKey(client, ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("policy_year_id", sa.String(36)),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("subject_id", sa.String(36), nullable=False),
            sa.Column("dedup_key", sa.String(180), nullable=False),
            sa.Column("recipient_email", sa.String(320), nullable=False),
            sa.Column("payload", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
            sa.Column("follow_up_on", sa.Date()),
            sa.Column("created_by", sa.String(36)),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("lease_token", sa.String(36)),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.String(255)),
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
            sa.UniqueConstraint("client_id", "dedup_key", name="uq_workflow_notifications_dedup"),
            sa.CheckConstraint(
                "status IN ('queued','sending','sent','dead','cancelled')",
                name="status_valid",
            ),
            schema=schema,
        )
        op.create_index(
            "ix_workflow_notifications_due",
            "workflow_notifications",
            ["status", "available_at"],
            schema=schema,
        )
        op.create_index(
            "ix_workflow_notifications_subject",
            "workflow_notifications",
            ["kind", "subject_id"],
            schema=schema,
        )


def downgrade() -> None:
    bind = op.get_bind()
    for schema in reversed(_schemas(bind)):
        if _has_table(bind, "workflow_notifications", schema):
            op.drop_table("workflow_notifications", schema=schema)
