"""backfill delegated claims created before the HR origin was available

Revision ID: f5a7c9e1b3d4
Revises: e4f6a8c0b2d3
Create Date: 2026-09-22

The partial HR endpoint marked delegated rows in ``intake_meta`` while the
model still defaulted their origin to ``portal``. Repair those rows in public
and every firm schema so member surfaces cannot expose or edit them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f5a7c9e1b3d4"
down_revision: str | None = "e4f6a8c0b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres_claim_schemas(bind: sa.engine.Connection) -> list[str]:
    firm_ids = bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars()
    candidates = [
        "public",
        *[
            "firm_" + "".join(char for char in str(firm_id) if char.isalnum())
            for firm_id in firm_ids
        ],
    ]
    return [
        schema
        for schema in candidates
        if bind.scalar(
            sa.text("SELECT to_regclass(:table_name) IS NOT NULL"),
            {"table_name": f"{schema}.claims"},
        )
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "UPDATE claims SET origin = 'hr' "
                "WHERE origin = 'portal' "
                "AND json_valid(intake_meta) "
                "AND json_extract(intake_meta, '$.submission_channel') = 'hr'"
            )
        )
        return
    for schema in _postgres_claim_schemas(bind):
        bind.execute(
            sa.text(
                f'UPDATE "{schema}".claims SET origin = \'hr\' '
                "WHERE origin = 'portal' "
                "AND intake_meta ->> 'submission_channel' = 'hr'"
            )
        )


def downgrade() -> None:
    # Deliberately irreversible. Reclassifying these rows as portal claims would
    # reintroduce the data exposure this repair closes. The preceding revision
    # already accepts ``hr``; downgrading past it safely maps HR rows to broker.
    pass
