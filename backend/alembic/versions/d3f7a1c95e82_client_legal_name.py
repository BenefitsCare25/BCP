"""clients.legal_name — the company's registered name

`clients.name` is the broker's internal short handle ("CDL") and is what every
broker-facing list prints. The member portal needs the name the member would
recognise as their employer, which nothing stored today holds: the registered
name only exists per-year, per-product, inside
`ProductSetup.answers["header"]["policyholder"]`, captured off an insurer's slip.

Nullable with no backfill — a short name is not a legal name and deriving one
would put a guess on the surface members read.

`clients` is a CONTROL table (public schema on Postgres, never per-firm), so
this needs no `provision_tenants` sync.

Revision ID: d3f7a1c95e82
Revises: c1e5a7b93d20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3f7a1c95e82"
down_revision = "c1e5a7b93d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("legal_name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "legal_name")
