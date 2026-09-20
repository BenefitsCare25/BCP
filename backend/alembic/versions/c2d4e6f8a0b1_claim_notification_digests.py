"""Company digest preferences and durable notification group leases.

Revision ID: c2d4e6f8a0b1
Revises: b1c3d5e7f9a2
"""
import sqlalchemy as sa
from alembic import op

revision = "c2d4e6f8a0b1"
down_revision = "b1c3d5e7f9a2"
branch_labels = depends_on = None


def _schemas(bind):
    if bind.dialect.name != "postgresql":
        return [None]
    result = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        if bind.scalar(sa.text("SELECT to_regclass(:name) IS NOT NULL"),
                       {"name": f"{schema}.claim_notifications"}):
            result.append(schema)
    return result


def upgrade():
    for schema in _schemas(op.get_bind()):
        client = "public.clients.id" if schema else "clients.id"
        op.create_table(
            "workflow_notification_settings",
            sa.Column("client_id", sa.String(36), sa.ForeignKey(client, ondelete="CASCADE"), primary_key=True),
            sa.Column("claim_delivery", sa.String(16), nullable=False),
            sa.Column("digest_minutes", sa.Integer(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("claim_delivery IN ('immediate', 'digest')", name="delivery_valid"),
            sa.CheckConstraint("digest_minutes BETWEEN 15 AND 1440", name="digest_interval_valid"),
            schema=schema,
        )
        op.add_column("claim_notifications", sa.Column("digest_key", sa.String(64)), schema=schema)
        op.add_column("claim_notifications", sa.Column("lease_token", sa.String(36)), schema=schema)
        op.create_index("ix_claim_notifications_digest_key", "claim_notifications", ["digest_key"], schema=schema)


def downgrade():
    for schema in reversed(_schemas(op.get_bind())):
        op.drop_index("ix_claim_notifications_digest_key", table_name="claim_notifications", schema=schema)
        op.drop_column("claim_notifications", "lease_token", schema=schema)
        op.drop_column("claim_notifications", "digest_key", schema=schema)
        op.drop_table("workflow_notification_settings", schema=schema)
