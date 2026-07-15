"""Normalize participation_model to controlled vocabulary.

Revision ID: e7f8a9b01234
Revises: d1e2f3a4b5c6
Create Date: 2026-06-16 10:00:00.000000

Maps free-text values captured from placement slips / manual entry to the
two canonical values: 'compulsory' or 'voluntary'.  Anything not recognised
is set to NULL so it can be re-entered correctly via the UI dropdown.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7f8a9b01234"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("""
            UPDATE categories
            SET participation_model = CASE
                WHEN LOWER(TRIM(participation_model))
                     IN ('compulsory', 'mandatory', 'required')
                    THEN 'compulsory'
                WHEN LOWER(TRIM(participation_model))
                     IN ('voluntary', 'optional')
                    THEN 'voluntary'
                ELSE NULL
            END
            WHERE participation_model IS NOT NULL
        """)
    )


def downgrade() -> None:
    # Values cannot be recovered — downgrade is a no-op.
    pass
