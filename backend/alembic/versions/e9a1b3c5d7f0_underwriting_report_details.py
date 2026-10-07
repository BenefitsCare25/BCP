"""Insurer correspondence and explicit annual premium details for Full EL."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "e9a1b3c5d7f0"
down_revision = "d8f0a2b4c6e8"
branch_labels = depends_on = None


def _schemas(bind):
    if bind.dialect.name != "postgresql":
        return [None]
    schemas = ["public"]
    for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
        if sa.inspect(bind).has_table("underwriting_cases", schema=schema):
            schemas.append(schema)
    return schemas


def upgrade():
    bind = op.get_bind()
    for schema in _schemas(bind):
        columns = {
            c["name"] for c in sa.inspect(bind).get_columns("underwriting_cases", schema=schema)
        }
        if "report_details" not in columns:
            op.add_column(
                "underwriting_cases",
                sa.Column(
                    "report_details",
                    sa.JSON().with_variant(JSONB(), "postgresql"),
                    nullable=True,
                ),
                schema=schema,
            )


def downgrade():
    bind = op.get_bind()
    for schema in _schemas(bind):
        op.drop_column("underwriting_cases", "report_details", schema=schema)
