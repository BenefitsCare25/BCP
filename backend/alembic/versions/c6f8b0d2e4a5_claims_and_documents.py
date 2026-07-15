"""Claims + retained documents

Tenant tables for the employee-portal claims module (Phase 2):

- ``claims`` — member-submitted insurance/flex claims with the
  draft → submitted → ai_* → approved/rejected/needs_info state machine.
- ``stored_documents`` — metadata for RETAINED uploads (claim receipts,
  dependant proofs); bytes live in the storage backend, SHA-256 here for
  duplicate/tampering detection.

Both auto-provision into firm schemas via ``tenancy.sync_firm_schema``.

Revision ID: c6f8b0d2e4a5
Revises: b5e7a9c1d3f4
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.migration_helpers import json_variant

revision: str = "c6f8b0d2e4a5"
down_revision: Union[str, None] = "b5e7a9c1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_year_id",
            sa.String(36),
            sa.ForeignKey("policy_years.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            sa.String(36),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "dependant_id",
            sa.String(36),
            sa.ForeignKey("dependants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("claim_kind", sa.String(16), nullable=False, server_default="insured"),
        sa.Column("product_code", sa.String(64), nullable=True),
        sa.Column("benefit_key", sa.String(255), nullable=True),
        sa.Column("flex_category_name", sa.String(255), nullable=True),
        sa.Column("claim_type", sa.String(64), nullable=False),
        sa.Column("incurred_date", sa.Date(), nullable=False),
        sa.Column("provider_name", sa.String(255), nullable=True),
        sa.Column("diagnosis", sa.String(512), nullable=True),
        sa.Column("amount_claimed", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="SGD"),
        sa.Column("amount_converted", sa.Float(), nullable=True),
        sa.Column("amount_approved", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_member_id", sa.String(36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(36), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("form_fields", json_variant(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_claims_client_id", "claims", ["client_id"])
    op.create_index("ix_claims_policy_year_id", "claims", ["policy_year_id"])
    op.create_index("ix_claims_employee_id", "claims", ["employee_id"])
    op.create_index("ix_claims_status", "claims", ["status"])
    op.create_index(
        "ix_claims_employee_year_status",
        "claims",
        ["employee_id", "policy_year_id", "status"],
    )

    op.create_table(
        "stored_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "client_id",
            sa.String(36),
            sa.ForeignKey("clients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("uploaded_by_member_id", sa.String(36), nullable=True),
        sa.Column("uploaded_by_user_id", sa.String(36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_stored_documents_client_id", "stored_documents", ["client_id"])
    op.create_index("ix_stored_documents_sha256", "stored_documents", ["sha256"])
    op.create_index(
        "ix_stored_documents_entity", "stored_documents", ["entity_type", "entity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_stored_documents_entity", table_name="stored_documents")
    op.drop_index("ix_stored_documents_sha256", table_name="stored_documents")
    op.drop_index("ix_stored_documents_client_id", table_name="stored_documents")
    op.drop_table("stored_documents")
    op.drop_index("ix_claims_employee_year_status", table_name="claims")
    op.drop_index("ix_claims_status", table_name="claims")
    op.drop_index("ix_claims_employee_id", table_name="claims")
    op.drop_index("ix_claims_policy_year_id", table_name="claims")
    op.drop_index("ix_claims_client_id", table_name="claims")
    op.drop_table("claims")
