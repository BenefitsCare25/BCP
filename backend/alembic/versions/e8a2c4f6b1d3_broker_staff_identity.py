"""Broker staff identity: per-firm sign-in methods, directory-scoped Entra ids.

Revision ID: e8a2c4f6b1d3
Revises: d4f6a8c2e1b9
Create Date: 2026-10-08

- New control table ``identity_providers``: each broker firm offers Microsoft
  365 (its own Entra directory), email + password, or both. When the platform
  directory is configured (``INSPRO_ENTRA_TENANT_ID``), the platform owner's
  firm is given an Entra provider for it, matching today's sign-in.
- ``users.external_tid``: the Entra directory of ``external_id``. The binding
  key becomes (directory, object id); rows bound before this keep NULL and are
  unique on their object id alone. Existing bindings are stamped with the
  platform directory when it is configured.
- Invitation tokens are stored as SHA-256 hashes from now on; existing
  plaintext tokens (never redeemable before this release) are hashed in place.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import sqlite_fk_guard

revision: str = "e8a2c4f6b1d3"
down_revision: str | None = "d4f6a8c2e1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _platform_directory() -> str:
    return os.environ.get("INSPRO_ENTRA_TENANT_ID", "").strip().lower()


def _create_identity_providers() -> None:
    op.create_table(
        "identity_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "broker_firm_id", sa.String(36),
            sa.ForeignKey("broker_firms.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("entra_tenant_id", sa.String(36), nullable=True),
        sa.Column(
            "require_platform_mfa", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("display_label", sa.String(80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("broker_firm_id", "kind", name="uq_identity_providers_firm_kind"),
    )
    op.create_index(
        "ix_identity_providers_broker_firm_id", "identity_providers", ["broker_firm_id"]
    )


def _seed_owner_entra(bind: sa.engine.Connection) -> None:
    tenant = _platform_directory()
    owner = bind.execute(
        sa.text("SELECT id FROM broker_firms WHERE is_platform_owner = :yes"), {"yes": True}
    ).scalar()
    if not tenant or owner is None:
        return
    bind.execute(
        sa.text(
            "INSERT INTO identity_providers (id, broker_firm_id, kind, enabled, entra_tenant_id, "
            "require_platform_mfa) VALUES (:id, :firm, 'entra', :yes, :tenant, :no)"
        ),
        {"id": str(uuid.uuid4()), "firm": owner, "tenant": tenant, "yes": True, "no": False},
    )


def _hash_invitation_tokens(bind: sa.engine.Connection) -> None:
    rows = bind.execute(sa.text("SELECT id, token FROM invitations")).all()
    for invitation_id, token in rows:
        digest = hashlib.sha256(str(token).encode()).hexdigest()
        bind.execute(
            sa.text("UPDATE invitations SET token = :digest WHERE id = :id"),
            {"digest": digest, "id": invitation_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    _create_identity_providers()
    _seed_owner_entra(bind)

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("external_tid", sa.String(36), nullable=True))
    tenant = _platform_directory()
    if tenant:
        bind.execute(
            sa.text("UPDATE users SET external_tid = :tid WHERE external_id IS NOT NULL"),
            {"tid": tenant},
        )
    if dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("users", recreate="always") as batch:
                batch.drop_constraint("uq_users_external_id", type_="unique")
    else:
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS uq_users_external_id")
    op.create_index(
        "uq_users_entra_identity", "users", ["external_tid", "external_id"], unique=True
    )
    op.create_index(
        "uq_users_legacy_external_id", "users", ["external_id"], unique=True,
        postgresql_where=sa.text("external_tid IS NULL"),
        sqlite_where=sa.text("external_tid IS NULL"),
    )

    _hash_invitation_tokens(bind)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.drop_index("uq_users_legacy_external_id", table_name="users")
    op.drop_index("uq_users_entra_identity", table_name="users")
    if dialect == "sqlite":
        with sqlite_fk_guard(bind):
            with op.batch_alter_table("users", recreate="always") as batch:
                batch.create_unique_constraint("uq_users_external_id", ["external_id"])
                batch.drop_column("external_tid")
    else:
        op.create_unique_constraint("uq_users_external_id", "users", ["external_id"])
        with op.batch_alter_table("users") as batch:
            batch.drop_column("external_tid")
    op.drop_index("ix_identity_providers_broker_firm_id", table_name="identity_providers")
    op.drop_table("identity_providers")
