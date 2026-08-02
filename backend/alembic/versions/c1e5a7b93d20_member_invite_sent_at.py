"""member_accounts invite delivery + one-time-password expiry

`invite_sent_at` is the durable "this member was emailed an invite" fact.
Targeting for the bulk portal invite is `invite_sent_at IS NULL`, so the send is
idempotent per member: a delivered invite is never re-sent, and a send that
failed (mail outage) stays NULL and is picked up by the next run.

`invite_expires_at` bounds the mailed one-time password. It is cleared the
moment the member (or a broker) sets a real password, so it only ever gates a
credential that is still sitting unused in a mailbox.

Revision ID: c1e5a7b93d20
Revises: b6d8f0a2c4e7
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1e5a7b93d20"
down_revision = "b6d8f0a2c4e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member_accounts",
        sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "member_accounts",
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("member_accounts", "invite_expires_at")
    op.drop_column("member_accounts", "invite_sent_at")
