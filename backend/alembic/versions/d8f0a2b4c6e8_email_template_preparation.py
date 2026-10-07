"""Template drafts, publications, branding and unsent recipient preparations."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "d8f0a2b4c6e8"
down_revision = "c7e9a1b3d5f7"
branch_labels = depends_on = None


def schemas(bind):
    if bind.dialect.name != "postgresql":
        return [None]
    result = ["public"]
    for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.employees"}
        ):
            result.append(schema)
    return result


def upgrade():
    bind = op.get_bind()
    js = sa.JSON().with_variant(JSONB(), "postgresql")
    for schema in schemas(bind):
        control = "public." if schema else ""
        tenant = f"{schema}." if schema else ""
        specs = {
            "email_templates": [
                sa.Column(
                    "broker_firm_id",
                    sa.String(36),
                    sa.ForeignKey(f"{control}broker_firms.id"),
                    nullable=False,
                ),
                sa.Column("client_id", sa.String(36), sa.ForeignKey(f"{control}clients.id")),
                sa.Column("scope_key", sa.String(40), nullable=False),
                sa.Column("template_key", sa.String(80), nullable=False),
                sa.Column("draft", js, nullable=False),
                sa.Column("revision", sa.Integer(), nullable=False),
                sa.Column("published_version", sa.Integer()),
                sa.UniqueConstraint("broker_firm_id", "scope_key", "template_key"),
            ],
            "email_template_versions": [
                sa.Column(
                    "template_id",
                    sa.String(36),
                    sa.ForeignKey(f"{tenant}email_templates.id"),
                    nullable=False,
                ),
                sa.Column("version", sa.Integer(), nullable=False),
                sa.Column("content", js, nullable=False),
                sa.Column("published_by", sa.String(36), nullable=False),
                sa.UniqueConstraint("template_id", "version"),
            ],
            "email_branding": [
                sa.Column(
                    "broker_firm_id",
                    sa.String(36),
                    sa.ForeignKey(f"{control}broker_firms.id"),
                    nullable=False,
                ),
                sa.Column("client_id", sa.String(36), sa.ForeignKey(f"{control}clients.id")),
                sa.Column("scope_key", sa.String(40), nullable=False),
                sa.Column("content", js, nullable=False),
                sa.Column("revision", sa.Integer(), nullable=False),
                sa.UniqueConstraint("broker_firm_id", "scope_key"),
            ],
            "email_preparations": [
                sa.Column(
                    "broker_firm_id",
                    sa.String(36),
                    sa.ForeignKey(f"{control}broker_firms.id"),
                    nullable=False,
                ),
                sa.Column(
                    "client_id",
                    sa.String(36),
                    sa.ForeignKey(f"{control}clients.id"),
                    nullable=False,
                ),
                sa.Column("request_key", sa.String(36), nullable=False),
                sa.Column("template_key", sa.String(80), nullable=False),
                sa.Column("template_title", sa.String(120), nullable=False),
                sa.Column("template_version", sa.String(100), nullable=False),
                sa.Column("snapshot", js, nullable=False),
                sa.Column("recipients", js, nullable=False),
                sa.Column("created_by", sa.String(36), nullable=False),
                sa.Column("status", sa.String(16), nullable=False),
                sa.UniqueConstraint("client_id", "request_key"),
            ],
        }
        for table, columns in specs.items():
            if sa.inspect(bind).has_table(table, schema=schema):
                continue
            op.create_table(
                table,
                sa.Column("id", sa.String(36), primary_key=True),
                *columns,
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
                schema=schema,
            )
            for column in ("broker_firm_id", "client_id", "template_id"):
                if any(getattr(item, "name", None) == column for item in columns):
                    op.create_index(f"ix_{table}_{column}", table, [column], schema=schema)


def downgrade():
    for schema in reversed(schemas(op.get_bind())):
        for table in (
            "email_preparations",
            "email_branding",
            "email_template_versions",
            "email_templates",
        ):
            if sa.inspect(op.get_bind()).has_table(table, schema=schema):
                op.drop_table(table, schema=schema)
