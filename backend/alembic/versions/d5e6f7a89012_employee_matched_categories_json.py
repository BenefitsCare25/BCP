"""Add matched_categories JSON column to employees for per-product matching

Revision ID: d5e6f7a89012
Revises: c4d8e9f12345
Create Date: 2026-05-25 12:00:00.000000

"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "d5e6f7a89012"
down_revision: Union[str, None] = "c4d8e9f12345"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("matched_categories", json_variant(), nullable=True))

    # Backfill: seed matched_categories from the existing single-match fields
    # so _hydrate_plans returns data immediately without a re-match run.
    conn = op.get_bind()
    employees = sa.table(
        "employees",
        sa.column("id", sa.String),
        sa.column("matched_category_id", sa.String),
        sa.column("match_method", sa.String),
        sa.column("match_confidence", sa.Float),
        sa.column("matched_categories", json_variant()),
    )
    categories = sa.table(
        "categories",
        sa.column("id", sa.String),
        sa.column("product_id", sa.String),
    )
    products = sa.table(
        "products",
        sa.column("id", sa.String),
        sa.column("code", sa.String),
    )

    rows = conn.execute(
        sa.select(
            employees.c.id,
            employees.c.matched_category_id,
            employees.c.match_method,
            employees.c.match_confidence,
            products.c.code,
        )
        .select_from(
            employees
            .join(categories, employees.c.matched_category_id == categories.c.id)
            .outerjoin(products, categories.c.product_id == products.c.id)
        )
        .where(employees.c.matched_category_id.isnot(None))
    ).fetchall()

    for emp_id, cat_id, method, confidence, product_code in rows:
        payload = json.dumps([{
            "category_id": cat_id,
            "product_code": product_code or "?",
            "method": method,
            "confidence": confidence,
        }])
        conn.execute(
            employees.update()
            .where(employees.c.id == emp_id)
            .values(matched_categories=payload)
        )


def downgrade() -> None:
    op.drop_column("employees", "matched_categories")
