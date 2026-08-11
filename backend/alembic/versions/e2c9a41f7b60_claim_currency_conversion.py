"""claim currency conversion

Foreign-currency claims are converted to the policy currency (SGD) at the ECB
reference rate for the RECEIPT date. This adds the trail that makes the figure
auditable, plus the shared rate cache behind it.

``claims``
    ``fx_rate`` / ``fx_rate_date``  the rate applied and the day it was published
                                    on. The publication date can precede the
                                    incurred date — the reference series has no
                                    weekend or holiday entries — so both are kept.
    ``fx_source``                   "frankfurter" or "broker" (hand-keyed when no
                                    rate could be fetched).
    ``fx_acknowledged_at``          when the CLAIMANT accepted the converted
                                    figure. Submit requires it on a convertible
                                    foreign claim.

    All NULL on existing rows, and that is honest: every claim in the system
    predates conversion. SGD claims — which is all of them in practice — need
    none of these columns, so nothing is backfilled.

``fx_rates`` (NEW, in ``public``)
    The rate cache. A CONTROL table: a rate is a fact about the market on a date
    and belongs to no firm, so it is deliberately NOT synced into the per-firm
    schemas (``db/tenancy.py::CONTROL_TABLES``). Two firms converting the same
    receipt must reach the same figure.

The ``claims`` columns are additive, so ``scripts/provision_tenants.py`` syncs
them into every firm schema on deploy.

Revision ID: e2c9a41f7b60
Revises: b6d2f4a8c1e5
Create Date: 2026-08-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e2c9a41f7b60"
down_revision = "b6d2f4a8c1e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("fx_rate", sa.Float(), nullable=True))
    op.add_column("claims", sa.Column("fx_rate_date", sa.Date(), nullable=True))
    op.add_column("claims", sa.Column("fx_source", sa.String(length=32), nullable=True))
    op.add_column(
        "claims",
        sa.Column("fx_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("base_currency", sa.String(length=8), nullable=False),
        sa.Column("quote_currency", sa.String(length=8), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_fx_rates"),
        # The lookup IS the uniqueness rule: one rate per currency pair per date.
        # Enforced in the database because concurrent submits race to insert the
        # same row, and the loser must find the winner's rather than add a second.
        sa.UniqueConstraint(
            "base_currency", "quote_currency", "as_of_date", name="uq_fx_rates_lookup"
        ),
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
    op.drop_column("claims", "fx_acknowledged_at")
    op.drop_column("claims", "fx_source")
    op.drop_column("claims", "fx_rate_date")
    op.drop_column("claims", "fx_rate")
