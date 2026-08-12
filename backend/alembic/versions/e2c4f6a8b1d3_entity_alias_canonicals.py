"""entity_aliases.canonicals — an alias may stand for several entities

Revision ID: e2c4f6a8b1d3
Revises: d5c8b1e70f26
Create Date: 2026-08-13

A single roster spelling ("STMICROELECTRONICS PTE LTD") can cover more than one
registered subsidiary ("… AMK", "… TPY"), each a separate insured block on the
slip. So an alias resolves to a SET of canonical spellings, stored in a new
JSON `canonicals` list.

Additive only: the column is NULLABLE and the unique constraint on
`(client_id, alias_normalized)` is UNCHANGED (still one row per alias — the row
now carries a list). Readers fall back to `[canonical]` when `canonicals` is
NULL, so pre-existing rows keep working with no data migration. The backfill
below only touches the schema Alembic points at (dev/test single-schema, or the
empty `public` template in prod); the per-firm schemas gain the column via
`provision_tenants` / `sync_firm_schema` and rely on the `[canonical]` fallback.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant

revision = "e2c4f6a8b1d3"
down_revision = "d5c8b1e70f26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entity_aliases",
        sa.Column("canonicals", json_variant(), nullable=True),
    )
    # Seed the list from the existing single canonical so a row that predates
    # this column reads identically whether or not the fallback fires.
    bind = op.get_bind()
    # jsonb_build_array (not json_build_array) so the value is jsonb on Postgres
    # — the column is jsonb there, and this avoids an implicit json→jsonb cast.
    json_array = "jsonb_build_array" if bind.dialect.name == "postgresql" else "json_array"
    op.execute(
        f"UPDATE entity_aliases SET canonicals = {json_array}(canonical) "
        "WHERE canonicals IS NULL"
    )


def downgrade() -> None:
    op.drop_column("entity_aliases", "canonicals")
