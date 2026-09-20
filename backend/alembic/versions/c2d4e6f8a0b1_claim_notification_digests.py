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
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"{schema}.claim_notifications"},
        ):
            result.append(schema)
    return result


def _has_table(bind, table, schema):
    return sa.inspect(bind).has_table(table, schema=schema)


def _has_column(bind, table, column, schema):
    if not _has_table(bind, table, schema):
        return False
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table, schema=schema)}


def _has_index(bind, table, index, schema):
    if not _has_table(bind, table, schema):
        return False
    return index in {item["name"] for item in sa.inspect(bind).get_indexes(table, schema=schema)}


def upgrade():
    bind = op.get_bind()
    for schema in _schemas(bind):
        client = "public.clients.id" if schema else "clients.id"
        if not _has_table(bind, "workflow_notification_settings", schema):
            op.create_table(
                "workflow_notification_settings",
                sa.Column(
                    "client_id",
                    sa.String(36),
                    sa.ForeignKey(client, ondelete="CASCADE"),
                    primary_key=True,
                ),
                sa.Column("claim_delivery", sa.String(16), nullable=False),
                sa.Column("digest_minutes", sa.Integer(), nullable=False),
                sa.Column("revision", sa.Integer(), nullable=False),
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
                sa.CheckConstraint(
                    "claim_delivery IN ('immediate', 'digest')", name="delivery_valid"
                ),
                sa.CheckConstraint(
                    "digest_minutes BETWEEN 15 AND 1440",
                    name="digest_interval_valid",
                ),
                schema=schema,
            )
        if not _has_column(bind, "claim_notifications", "digest_key", schema):
            op.add_column(
                "claim_notifications",
                sa.Column("digest_key", sa.String(64)),
                schema=schema,
            )
        if not _has_column(bind, "claim_notifications", "lease_token", schema):
            op.add_column(
                "claim_notifications",
                sa.Column("lease_token", sa.String(36)),
                schema=schema,
            )
        if not _has_index(
            bind,
            "claim_notifications",
            "ix_claim_notifications_digest_key",
            schema,
        ):
            op.create_index(
                "ix_claim_notifications_digest_key",
                "claim_notifications",
                ["digest_key"],
                schema=schema,
            )


def downgrade():
    bind = op.get_bind()
    for schema in reversed(_schemas(bind)):
        if _has_index(
            bind,
            "claim_notifications",
            "ix_claim_notifications_digest_key",
            schema,
        ):
            op.drop_index(
                "ix_claim_notifications_digest_key",
                table_name="claim_notifications",
                schema=schema,
            )
        if _has_column(bind, "claim_notifications", "lease_token", schema):
            op.drop_column("claim_notifications", "lease_token", schema=schema)
        if _has_column(bind, "claim_notifications", "digest_key", schema):
            op.drop_column("claim_notifications", "digest_key", schema=schema)
        if _has_table(bind, "workflow_notification_settings", schema):
            op.drop_table("workflow_notification_settings", schema=schema)
