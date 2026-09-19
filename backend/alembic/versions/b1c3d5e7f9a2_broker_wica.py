"""Add broker-only WICA tables to public and every provisioned firm schema.

Revision ID: b1c3d5e7f9a2
Revises: a0b2d4f6c8e1
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b1c3d5e7f9a2"
down_revision = "a0b2d4f6c8e1"
branch_labels = depends_on = None


def _schemas(bind):
    if bind.dialect.name != "postgresql":
        return [None]
    schemas = ["public"]
    for firm_id in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(firm_id) if c.isalnum())
        if bind.scalar(
            sa.text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"{schema}.employees"}
        ):
            schemas.append(schema)
    return schemas


def _create(schema):
    prefix = f"{schema}." if schema else ""
    client = "public.clients.id" if schema else "clients.id"
    js = sa.JSON().with_variant(JSONB(), "postgresql")

    def timestamps():
        return [
            sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
            for n in ("created_at", "updated_at")
        ]

    def ident():
        return sa.Column("id", sa.String(36), primary_key=True)

    def client_col():
        return sa.Column("client_id", sa.String(36), sa.ForeignKey(client), nullable=False)

    op.create_table(
        "wica_settings",
        sa.Column("client_id", sa.String(36), sa.ForeignKey(client), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *timestamps(),
        schema=schema,
    )
    op.create_table(
        "wica_periods",
        ident(),
        client_col(),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("grace_days", sa.Integer()),
        *timestamps(),
        sa.CheckConstraint("end_date >= start_date", name="dates_valid"),
        sa.CheckConstraint("grace_days IS NULL OR grace_days >= 0", name="grace_valid"),
        schema=schema,
    )
    op.create_table(
        "wica_incidents",
        ident(),
        client_col(),
        sa.Column(
            "period_id", sa.String(36), sa.ForeignKey(prefix + "wica_periods.id"), nullable=False
        ),
        sa.Column(
            "employee_id",
            sa.String(36),
            sa.ForeignKey(prefix + "employees.id", ondelete="SET NULL"),
        ),
        sa.Column("employee_name", sa.String(255), nullable=False),
        sa.Column("staff_id", sa.String(128), nullable=False),
        sa.Column("incident_date", sa.Date(), nullable=False),
        sa.Column("report_number", sa.String(128), nullable=False),
        sa.Column("remarks", sa.String(4000), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *timestamps(),
        schema=schema,
    )
    op.create_table(
        "wica_documents",
        ident(),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey(prefix + "wica_incidents.id"),
            nullable=False,
        ),
        sa.Column(
            "stored_document_id",
            sa.String(36),
            sa.ForeignKey(prefix + "stored_documents.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("doc_type", sa.String(64)),
        sa.Column("document_date", sa.Date()),
        sa.Column("benefit_type", sa.String(64)),
        sa.Column("claim_id", sa.String(40), unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("related_ids", js, nullable=False),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("invoice_number", sa.String(128), nullable=False),
        sa.Column("incurred_amount", sa.Numeric(14, 2)),
        sa.Column("settlement_amount", sa.Numeric(14, 2)),
        sa.Column("settlement_date", sa.Date()),
        sa.Column("insurer_reference", sa.String(128), nullable=False),
        sa.Column("remarks", sa.String(2000), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "status IN ('untagged','supporting','submitted',"
            "'pending_insurer','settled','rejected')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "settlement_amount IS NULL OR settlement_amount >= 0", name="amount_valid"
        ),
        schema=schema,
    )
    op.create_table(
        "wica_packs",
        ident(),
        sa.Column(
            "incident_id",
            sa.String(36),
            sa.ForeignKey(prefix + "wica_incidents.id"),
            nullable=False,
        ),
        sa.Column("manifest", js, nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("sent_on", sa.Date()),
        sa.Column("sent_reference", sa.String(255), nullable=False),
        *timestamps(),
        schema=schema,
    )
    for table, cols in {
        "wica_periods": ["client_id"],
        "wica_incidents": ["client_id", "period_id", "incident_date"],
        "wica_documents": ["incident_id"],
        "wica_packs": ["incident_id"],
    }.items():
        for col in cols:
            op.create_index(f"ix_{table}_{col}", table, [col], schema=schema)


def upgrade():
    for schema in _schemas(op.get_bind()):
        _create(schema)


def downgrade():
    # Refuse loss of retained claims. Production rollback disables the feature
    # and reverts the app, leaving these additive tables intact.
    bind = op.get_bind()
    schemas = _schemas(bind)
    for schema in schemas:
        prefix = f'"{schema}".' if schema else ""
        if bind.scalar(sa.text(f"SELECT count(*) FROM {prefix}wica_incidents")):
            raise RuntimeError(
                "WICA contains retained incidents; "
                "disable the feature instead of dropping its tables."
            )
    for schema in reversed(schemas):
        for table in (
            "wica_packs",
            "wica_documents",
            "wica_incidents",
            "wica_periods",
            "wica_settings",
        ):
            op.drop_table(table, schema=schema)
