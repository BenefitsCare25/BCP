"""Backfill clients.slug so existing tenants get working subdomains.

`d1f3a5c7e9b2` added the column but left every row NULL, and nothing wrote to
it — so `resolve_tenant_context` (which looks a tenant up by `clients.slug`)
404'd for EVERY company, making the HR surface and portal credential login
unreachable in any deployment that already had clients.

Derives the same label `services.client_slug` produces for new clients, so
backfilled and freshly-created tenants are consistent. `clients` is a control
table in `public`, so this runs once rather than per firm schema.

Revision ID: a3c5e7b9d1f4
Revises: f4c6e8a0b2d3
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7b9d1f4"
down_revision: Union[str, None] = "f4c6e8a0b2d3"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Import lazily: migrations must not depend on app import order at module
    # scope, but reusing the real generator keeps one definition of "slug".
    from app.services.client_slug import slugify_client_name

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, name FROM clients WHERE slug IS NULL ORDER BY id")
    ).fetchall()
    if not rows:
        return
    taken = {
        s for (s,) in conn.execute(
            sa.text("SELECT slug FROM clients WHERE slug IS NOT NULL")
        )
    }
    for client_id, name in rows:
        base = slugify_client_name(name or "")
        slug = base
        n = 2
        while slug in taken:
            slug = f"{base[:60]}-{n}"
            n += 1
        taken.add(slug)
        conn.execute(
            sa.text("UPDATE clients SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": client_id},
        )


def downgrade() -> None:
    # The column itself belongs to d1f3a5c7e9b2; only clear what we filled.
    op.execute(sa.text("UPDATE clients SET slug = NULL"))
