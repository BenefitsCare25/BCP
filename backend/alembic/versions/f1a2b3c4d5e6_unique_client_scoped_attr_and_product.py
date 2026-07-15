"""unique (client_id, attribute_id) and (client_id, code)

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8901234
Create Date: 2026-05-26 12:00:00.000000

Prevents duplicate client-scoped rows (e.g. two apply-config requests racing to
create the same attribute/product). Global rows (client_id NULL) are exempt —
SQL treats NULLs as distinct, which matches the one-global-default model.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e6f7a8901234"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employee_attribute_schemas", schema=None) as batch_op:
        batch_op.create_unique_constraint(
            "uq_emp_attr_client_attribute", ["client_id", "attribute_id"]
        )
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.create_unique_constraint("uq_product_client_code", ["client_id", "code"])


def downgrade() -> None:
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_constraint("uq_product_client_code", type_="unique")
    with op.batch_alter_table("employee_attribute_schemas", schema=None) as batch_op:
        batch_op.drop_constraint("uq_emp_attr_client_attribute", type_="unique")
