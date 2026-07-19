"""insured-entity alias map

Revision ID: d1f3b5a7c9e2
Revises: b8d0f2a4c6e9
Create Date: 2026-07-19

Additive only. `plan_assignments.insured` and the roster's `entity` attribute
stay free text — this table only changes how the two COMPARE, so the placement
slip keeps exporting the legal spelling (see app/models/entity_alias.py).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1f3b5a7c9e2"
down_revision = "b8d0f2a4c6e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("canonical", sa.String(length=255), nullable=False),
        sa.Column("alias_normalized", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # Compared in normalized form so "C.S.O." and "CSO" can't both map.
        sa.UniqueConstraint(
            "client_id", "alias_normalized", name="uq_entity_alias_client_alias"
        ),
    )
    op.create_index(
        op.f("ix_entity_aliases_client_id"), "entity_aliases", ["client_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_entity_aliases_client_id"), table_name="entity_aliases")
    op.drop_table("entity_aliases")
