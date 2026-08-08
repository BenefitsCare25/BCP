"""claims: reference number + the insurer settlement leg

Revision ID: e7a2b9c4d150
Revises: d5b8e1a4c7f2
Create Date: 2026-08-08

Adds the columns behind `services/claim_settlement.py` — the part of a claim's
life that happens after we accept it and before the member is paid.

All nullable, no backfill, and deliberately so:

- `reference_no` is minted at SUBMIT. Backfilling one onto historic claims
  would mint references nobody was ever told, and the member cannot quote a
  number that never appeared on their screen. Existing claims keep None and
  report blank; anything submitted from here on carries one.
- the settlement dates describe events that did not happen for claims decided
  before this leg existed. A default would assert a dispatch date we do not
  have.
- `taxable` / `cpf_claimable` are tri-state on purpose: NULL is "not assessed",
  which is a different fact from an assessor deciding "no".

No new statuses need a data migration — `sent_to_insurer` and `paid` are only
ever reached forward, from `approved`.
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e7a2b9c4d150"
down_revision = "d5b8e1a4c7f2"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("reference_no", sa.String(32)),
    ("sent_to_insurer_at", sa.DateTime(timezone=True)),
    ("sent_to_insurer_by", sa.String(36)),
    ("insurer_deadline_on", sa.Date()),
    ("paid_on", sa.Date()),
    ("payment_amount", sa.Float()),
    ("hospital_type", sa.String(16)),
    ("admission_date", sa.Date()),
    ("discharge_date", sa.Date()),
    ("taxable", sa.Boolean()),
    ("cpf_claimable", sa.Boolean()),
    ("admin_remarks", sa.Text()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("claims", sa.Column(name, type_, nullable=True))
    # UNIQUE, not merely indexed. `mint_reference_no` reads the current max and
    # writes one past it, which races: two members submitting in the same moment
    # — ordinary during a portal rollout, and guaranteed across App Service
    # instances — would otherwise both be handed the same reference, silently.
    # That string is the key a broker reconciles against the insurer's ledger.
    # The constraint is what prevents the duplicate; the service retries on the
    # IntegrityError it raises.
    #
    # Nullable columns are exempt from uniqueness on both SQLite and Postgres,
    # so every pre-existing claim (all of which carry NULL — this migration adds
    # no backfill) is unaffected.
    op.create_index(
        "ix_claims_reference_no", "claims", ["reference_no"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_claims_reference_no", table_name="claims")
    for name, _type in reversed(_COLUMNS):
        op.drop_column("claims", name)
