"""Multi-broker control plane: firm lifecycle, customer domains, platform access.

Revision ID: d4f6a8c2e1b9
Revises: b6d4f2a8c1e3
Create Date: 2026-10-08

- ``broker_firms`` gains ``slug``, ``status``, ``is_platform_owner``,
  ``allow_hide_attribution`` and ``database_key``. Existing firms get a slug
  derived from their name; the single existing firm (or the local demo firm,
  else the oldest) becomes the platform owner.
- Company aliases (``clients.slug``) become unique per broker firm instead of
  platform-wide: the request host now names the firm.
- ``users.email`` becomes unique per broker firm (and among firm-less platform
  admins) instead of platform-wide.
- New control tables ``tenant_domains``, ``platform_access_grants`` and the
  append-only ``platform_audit_log``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import json_variant, sqlite_fk_guard

revision: str = "d4f6a8c2e1b9"
down_revision: str | None = "b6d4f2a8c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEMO_FIRM_ID = "00000000-0000-0000-0000-000000000010"
# Labels a firm slug must never take (platform hosts, conventional names).
_RESERVED = frozenset({
    "broker", "hr", "portal", "www", "api", "admin", "app", "auth", "login",
    "signin", "sign-in", "static", "assets", "cdn", "mail", "smtp", "ftp", "ns",
    "ns1", "ns2", "mx", "test", "staging", "stg", "dev", "demo", "internal",
    "system", "root", "status", "health", "inspro", "support", "help", "docs",
    "platform",
})


def _slugify(name: str, firm_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40].strip("-")
    if not slug or slug in _RESERVED:
        slug = "firm-" + "".join(c for c in firm_id.lower() if c.isalnum())[:8]
    return slug


def _backfill_firms(bind: sa.engine.Connection) -> None:
    firms = bind.execute(
        sa.text("SELECT id, name, created_at FROM broker_firms ORDER BY created_at, id")
    ).all()
    taken: set[str] = set()
    for firm_id, name, _created in firms:
        base = _slugify(name, str(firm_id))
        slug, n = base, 2
        while slug in taken:
            slug = f"{base}-{n}"
            n += 1
        taken.add(slug)
        bind.execute(
            sa.text("UPDATE broker_firms SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": firm_id},
        )
    if not firms:
        return
    ids = [str(row[0]) for row in firms]
    owner = ids[0] if len(ids) == 1 else (_DEMO_FIRM_ID if _DEMO_FIRM_ID in ids else ids[0])
    bind.execute(
        sa.text("UPDATE broker_firms SET is_platform_owner = :yes WHERE id = :id"),
        {"yes": True, "id": owner},
    )


def _assert_no_duplicate_company_slugs(bind: sa.engine.Connection) -> None:
    clash = bind.execute(
        sa.text(
            "SELECT broker_firm_id, slug FROM clients WHERE slug IS NOT NULL "
            "GROUP BY broker_firm_id, slug HAVING COUNT(*) > 1"
        )
    ).first()
    if clash is not None:
        raise RuntimeError(
            f"Two companies in firm {clash[0]} share the alias {clash[1]!r}; "
            "rename one before migrating."
        )


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _create_platform_tables() -> None:
    op.create_table(
        "tenant_domains",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "broker_firm_id", sa.String(36),
            sa.ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False, server_default="all"),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_tenant_domains_broker_firm_id", "tenant_domains", ["broker_firm_id"])
    op.create_index("ix_tenant_domains_hostname", "tenant_domains", ["hostname"], unique=True)

    op.create_table(
        "platform_access_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id", sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "broker_firm_id", sa.String(36),
            sa.ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(36), nullable=True),
        sa.Column("created_ip", sa.String(64), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_platform_access_grants_user_id", "platform_access_grants", ["user_id"])
    op.create_index(
        "ix_platform_access_grants_broker_firm_id", "platform_access_grants", ["broker_firm_id"]
    )
    op.create_index(
        "ix_platform_access_grants_user_firm", "platform_access_grants",
        ["user_id", "broker_firm_id"],
    )

    op.create_table(
        "platform_audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("broker_firm_id", sa.String(36), nullable=True),
        sa.Column("client_id", sa.String(36), nullable=True),
        sa.Column("detail", json_variant(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("request_id", sa.String(200), nullable=True),
    )
    op.create_index("ix_platform_audit_log_occurred_at", "platform_audit_log", ["occurred_at"])
    op.create_index("ix_platform_audit_log_action", "platform_audit_log", ["action"])
    op.create_index(
        "ix_platform_audit_log_broker_firm_id", "platform_audit_log", ["broker_firm_id"]
    )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    with op.batch_alter_table("broker_firms") as batch:
        batch.add_column(sa.Column("slug", sa.String(63), nullable=True))
        batch.add_column(
            sa.Column("status", sa.String(16), nullable=False, server_default="active")
        )
        batch.add_column(
            sa.Column("is_platform_owner", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column(
                "allow_hide_attribution", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column("database_key", sa.String(32), nullable=False, server_default="default")
        )
    _backfill_firms(bind)
    op.create_index("ix_broker_firms_slug", "broker_firms", ["slug"], unique=True)
    op.create_index(
        "uq_broker_firms_platform_owner", "broker_firms", ["is_platform_owner"], unique=True,
        postgresql_where=sa.text("is_platform_owner"),
        sqlite_where=sa.text("is_platform_owner = 1"),
    )

    _assert_no_duplicate_company_slugs(bind)
    op.execute("DROP INDEX IF EXISTS ix_clients_slug")
    op.create_index("ix_clients_slug", "clients", ["slug"])
    op.create_index("uq_clients_firm_slug", "clients", ["broker_firm_id", "slug"], unique=True)

    if dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("users", recreate="always") as batch:
                batch.drop_constraint("uq_users_email", type_="unique")
    else:
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_email")
    op.create_index("uq_users_firm_email", "users", ["broker_firm_id", "email"], unique=True)
    op.create_index(
        "uq_users_platform_email", "users", ["email"], unique=True,
        postgresql_where=sa.text("broker_firm_id IS NULL"),
        sqlite_where=sa.text("broker_firm_id IS NULL"),
    )

    _create_platform_tables()

    if dialect == "postgresql":
        op.execute(
            "CREATE OR REPLACE FUNCTION public.inspro_prevent_audit_mutation() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'audit_log is append-only'; END; $$"
        )
        op.execute(
            "CREATE TRIGGER platform_audit_log_append_only BEFORE UPDATE OR DELETE "
            "ON public.platform_audit_log FOR EACH ROW "
            "EXECUTE FUNCTION public.inspro_prevent_audit_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS platform_audit_log_append_only ON public.platform_audit_log"
        )
    op.drop_table("platform_audit_log")
    op.drop_table("platform_access_grants")
    op.drop_table("tenant_domains")

    op.drop_index("uq_users_platform_email", table_name="users")
    op.drop_index("uq_users_firm_email", table_name="users")
    if dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("users", recreate="always") as batch:
                batch.create_unique_constraint("uq_users_email", ["email"])
    else:
        op.create_unique_constraint("uq_users_email", "users", ["email"])

    op.drop_index("uq_clients_firm_slug", table_name="clients")
    op.drop_index("ix_clients_slug", table_name="clients")
    op.create_index("ix_clients_slug", "clients", ["slug"], unique=True)

    op.drop_index("uq_broker_firms_platform_owner", table_name="broker_firms")
    op.drop_index("ix_broker_firms_slug", table_name="broker_firms")
    with op.batch_alter_table("broker_firms") as batch:
        for column in (
            "database_key", "allow_hide_attribution", "is_platform_owner", "status", "slug"
        ):
            batch.drop_column(column)
