"""insurer name catalog

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f0
Create Date: 2026-07-19

Additive only — no existing insurer column is touched. ``Product.insurer`` and
friends stay free-text strings; this table is the vocabulary that feeds their
dropdown (see app/models/insurer.py for why it is not a foreign key).

Seeding the ~20 Singapore library rows is left to
``scripts/seed_insurers.py`` so it stays re-runnable and matches how the global
product catalog is seeded.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision = "b8d0f2a4c6e9"
down_revision = "a7c9e1b3d5f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insurers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("aliases", json_variant(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # NULL client_id rows are the shared library; SQL treats NULLs as
        # distinct, so a client may shadow a library name with its own entry.
        sa.UniqueConstraint("client_id", "name", name="uq_insurer_client_name"),
    )
    op.create_index(op.f("ix_insurers_client_id"), "insurers", ["client_id"])
    op.create_index(op.f("ix_insurers_name"), "insurers", ["name"])


def downgrade() -> None:
    op.drop_index(op.f("ix_insurers_name"), table_name="insurers")
    op.drop_index(op.f("ix_insurers_client_id"), table_name="insurers")
    op.drop_table("insurers")
