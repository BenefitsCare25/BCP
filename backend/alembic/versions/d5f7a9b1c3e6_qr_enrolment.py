"""No-login QR enrolment: shared form entries, public intakes, HR download
batches and their link + PIN grants. Additive tenant tables only."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "d5f7a9b1c3e6"
down_revision = "c3e5f7a9b1d4"
branch_labels = depends_on = None

_TABLES = (
    "hr_download_grants",
    "hr_download_batches",
    "public_intakes",
    "public_form_entries",
)


def _schemas(bind) -> list[str | None]:
    if bind.dialect.name != "postgresql":
        return [None]
    schemas: list[str | None] = ["public"]
    for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
        schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
        if sa.inspect(bind).has_table("enrollment_windows", schema=schema):
            schemas.append(schema)
    return schemas


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _client_fk(schema: str | None) -> sa.ForeignKey:
    return sa.ForeignKey("public.clients.id" if schema else "clients.id", ondelete="CASCADE")


def upgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        prefix = f"{schema}." if schema else ""
        inspector = sa.inspect(bind)
        # Model-based firm provisioning may already have created these tables.
        if not inspector.has_table("public_form_entries", schema=schema):
            op.create_table(
                "public_form_entries",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("client_id", sa.String(36), _client_fk(schema), nullable=False),
                sa.Column(
                    "policy_year_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}policy_years.id", ondelete="CASCADE"), nullable=False,
                ),
                sa.Column(
                    "window_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}enrollment_windows.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("token_hash", sa.String(64), nullable=False),
                sa.Column("token_enc", sa.Text(), nullable=False),
                sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
                sa.Column("created_by", sa.String(36)),
                sa.Column("rotated_at", sa.DateTime(timezone=True)),
                *_timestamps(),
                sa.UniqueConstraint("window_id", name="uq_public_form_entries_window"),
                sa.UniqueConstraint("token_hash", name="uq_public_form_entries_token_hash"),
                schema=schema,
            )
            op.create_index(
                "ix_public_form_entries_client_id", "public_form_entries", ["client_id"],
                schema=schema,
            )
        if not inspector.has_table("public_intakes", schema=schema):
            op.create_table(
                "public_intakes",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("client_id", sa.String(36), _client_fk(schema), nullable=False),
                sa.Column(
                    "policy_year_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}policy_years.id", ondelete="CASCADE"), nullable=False,
                ),
                sa.Column(
                    "window_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}enrollment_windows.id", ondelete="SET NULL"),
                ),
                sa.Column("entry_id", sa.String(36), nullable=False),
                sa.Column("reference_no", sa.String(32), nullable=False),
                sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
                sa.Column("claimed_staff_id", sa.String(128)),
                sa.Column("claimed_name", sa.String(255)),
                sa.Column("claimed_national_id", sa.String(32)),
                sa.Column("claimed_category_id", sa.String(36)),
                sa.Column("snapshot", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
                sa.Column("content_sha256", sa.String(64), nullable=False),
                sa.Column("request_id", sa.String(64), nullable=False),
                sa.Column("request_hash", sa.String(64), nullable=False),
                sa.Column("document_id", sa.String(36)),
                sa.Column("signer_ip", sa.String(64)),
                sa.Column("signer_user_agent", sa.String(512)),
                sa.Column("copy_key_hash", sa.String(64)),
                sa.Column("copy_expires_at", sa.DateTime(timezone=True)),
                sa.Column("copy_used_at", sa.DateTime(timezone=True)),
                sa.Column("linked_employee_id", sa.String(36)),
                sa.Column("linked_submission_id", sa.String(36)),
                sa.Column("linked_by", sa.String(36)),
                sa.Column("linked_at", sa.DateTime(timezone=True)),
                sa.Column("rejected_reason", sa.Text()),
                sa.Column("rejected_by", sa.String(36)),
                sa.Column("rejected_at", sa.DateTime(timezone=True)),
                *_timestamps(),
                sa.UniqueConstraint(
                    "client_id", "reference_no", name="uq_public_intakes_reference"
                ),
                sa.UniqueConstraint("entry_id", "request_id", name="uq_public_intakes_request"),
                schema=schema,
            )
            op.create_index(
                "ix_public_intakes_client_id", "public_intakes", ["client_id"], schema=schema
            )
            op.create_index(
                "ix_public_intakes_window_status", "public_intakes", ["window_id", "status"],
                schema=schema,
            )
        if not inspector.has_table("hr_download_batches", schema=schema):
            op.create_table(
                "hr_download_batches",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("client_id", sa.String(36), _client_fk(schema), nullable=False),
                sa.Column(
                    "policy_year_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}policy_years.id", ondelete="CASCADE"), nullable=False,
                ),
                sa.Column(
                    "window_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}enrollment_windows.id", ondelete="SET NULL"),
                ),
                sa.Column("label", sa.String(160), nullable=False),
                sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
                sa.Column("manifest", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
                sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("zip_path", sa.String(512)),
                sa.Column("zip_sha256", sa.String(64)),
                sa.Column("created_by", sa.String(36)),
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("closed_at", sa.DateTime(timezone=True)),
                sa.Column("closed_by", sa.String(36)),
                *_timestamps(),
                schema=schema,
            )
            op.create_index(
                "ix_hr_download_batches_client_id", "hr_download_batches", ["client_id"],
                schema=schema,
            )
        if not inspector.has_table("hr_download_grants", schema=schema):
            op.create_table(
                "hr_download_grants",
                sa.Column("id", sa.String(36), primary_key=True),
                sa.Column("client_id", sa.String(36), _client_fk(schema), nullable=False),
                sa.Column(
                    "batch_id", sa.String(36),
                    sa.ForeignKey(f"{prefix}hr_download_batches.id", ondelete="CASCADE"),
                    nullable=False,
                ),
                sa.Column("token_hash", sa.String(64), nullable=False),
                sa.Column("pin_hash", sa.String(255), nullable=False),
                sa.Column("pin_failures", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("locked_at", sa.DateTime(timezone=True)),
                sa.Column("recipient_note", sa.String(255)),
                sa.Column("issued_by", sa.String(36)),
                sa.Column("unused_expires_at", sa.DateTime(timezone=True), nullable=False),
                sa.Column("activated_at", sa.DateTime(timezone=True)),
                sa.Column("expires_at", sa.DateTime(timezone=True)),
                sa.Column("session_hash", sa.String(64)),
                sa.Column("downloads", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("last_download_at", sa.DateTime(timezone=True)),
                sa.Column("revoked_at", sa.DateTime(timezone=True)),
                sa.Column("revoked_by", sa.String(36)),
                sa.Column("revoke_reason", sa.String(16)),
                *_timestamps(),
                sa.UniqueConstraint("token_hash", name="uq_hr_download_grants_token_hash"),
                schema=schema,
            )
            op.create_index(
                "ix_hr_download_grants_client_id", "hr_download_grants", ["client_id"],
                schema=schema,
            )
            op.create_index(
                "ix_hr_download_grants_batch", "hr_download_grants", ["batch_id"], schema=schema
            )


def downgrade() -> None:
    bind = op.get_bind()
    for schema in _schemas(bind):
        for table in _TABLES:
            if sa.inspect(bind).has_table(table, schema=schema):
                op.drop_table(table, schema=schema)
