"""clients.slug + per-surface subdomain kill-switches.

Tenant-per-subdomain routing: `{slug}.portal.<base>` / `{slug}.hr.<base>`.
`slug` is nullable (backfilled per tenant before its subdomains go live);
`portal_enabled` / `hr_enabled` default true so existing tenants are unaffected.

Revision ID: d1f3a5c7e9b2
Revises: c3f7a9e1b5d2
Create Date: 2026-07-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1f3a5c7e9b2"
down_revision = "c3f7a9e1b5d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("slug", sa.String(63), nullable=True))
    op.create_index("ix_clients_slug", "clients", ["slug"], unique=True)
    op.add_column(
        "clients",
        sa.Column(
            "portal_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "clients",
        sa.Column(
            "hr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "hr_enabled")
    op.drop_column("clients", "portal_enabled")
    op.drop_index("ix_clients_slug", table_name="clients")
    op.drop_column("clients", "slug")
