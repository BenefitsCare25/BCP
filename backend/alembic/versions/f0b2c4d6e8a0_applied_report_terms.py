"""Persist applied report rules and distinguish configured terms from missing values."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "f0b2c4d6e8a0"
down_revision = "e9a1b3c5d7f0"
branch_labels = depends_on = None


def _schemas(bind):
    if bind.dialect.name != "postgresql":
        return [None]
    schemas = ["public"]
    for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
        if sa.inspect(bind).has_table("product_terms", schema=schema):
            schemas.append(schema)
    return schemas


def upgrade():
    bind = op.get_bind()
    for schema in _schemas(bind):
        columns = {c["name"] for c in sa.inspect(bind).get_columns("product_terms", schema=schema)}
        for name in ("report_rules", "configured_fields"):
            if name not in columns:
                op.add_column(
                    "product_terms",
                    sa.Column(name, sa.JSON().with_variant(JSONB(), "postgresql"), nullable=True),
                    schema=schema,
                )


def downgrade():
    for schema in _schemas(op.get_bind()):
        op.drop_column("product_terms", "configured_fields", schema=schema)
        op.drop_column("product_terms", "report_rules", schema=schema)
