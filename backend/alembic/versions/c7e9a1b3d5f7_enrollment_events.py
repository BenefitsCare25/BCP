"""Durable enrolment notices in each firm schema; preserve signed evidence."""

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "c7e9a1b3d5f7"
down_revision = "b6d8f0a2c4e6"
branch_labels = depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    schemas: list[str | None] = [None]
    if bind.dialect.name == "postgresql":
        schemas = ["public"]
        for ident in bind.execute(sa.text("SELECT id FROM public.broker_firms")).scalars():
            schema = "firm_" + "".join(c for c in str(ident) if c.isalnum())
            if sa.inspect(bind).has_table("enrollments", schema=schema):
                schemas.append(schema)
    for schema in schemas:
        prefix = f"{schema}." if schema else ""
        events = op.create_table(
            "enrollment_events",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "client_id",
                sa.String(36),
                sa.ForeignKey("public.clients.id" if schema else "clients.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "employee_id",
                sa.String(36),
                sa.ForeignKey(f"{prefix}employees.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "enrollment_id",
                sa.String(36),
                sa.ForeignKey(f"{prefix}enrollments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("reason", sa.Text()),
            sa.Column("actor_id", sa.String(36)),
            sa.Column("read_at", sa.DateTime(timezone=True)),
            sa.Column("notification_id", sa.String(36)),
            sa.Column("email_unavailable_reason", sa.String(255)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            schema=schema,
        )
        op.create_index(
            "ix_enrollment_events_employee",
            "enrollment_events",
            ["client_id", "employee_id"],
            schema=schema,
        )
        _backfill_cancelled(bind, schema, events)


def _backfill_cancelled(bind, schema, events) -> None:
    """Recover only unresolved resets proven by the audit trail; never send old mail."""
    metadata = sa.MetaData()

    def table(name):
        return sa.Table(name, metadata, schema=schema, autoload_with=bind, resolve_fks=False)

    audits, enrollments, forms = (
        table(name) for name in ("audit_log", "enrollments", "enrollment_form_submissions")
    )
    rows = bind.execute(
        sa.select(
            enrollments.c.id,
            enrollments.c.client_id,
            enrollments.c.employee_id,
            sa.func.max(audits.c.created_at).label("cancelled_at"),
        )
        .join(audits, audits.c.entity_id == enrollments.c.id)
        .where(
            enrollments.c.status == "not_started",
            audits.c.action == "reset_enrollment",
            audits.c.entity_type == "enrollment",
            audits.c.client_id == enrollments.c.client_id,
        )
        .group_by(enrollments.c.id, enrollments.c.client_id, enrollments.c.employee_id)
    ).all()
    for row in rows:
        affected = sa.and_(
            forms.c.enrollment_id == row.id,
            forms.c.status.in_(("submitted", "acknowledged")),
            sa.func.coalesce(forms.c.signed_at, forms.c.created_at) <= row.cancelled_at,
        )
        if bind.scalar(sa.select(forms.c.id).where(affected).limit(1)) is None:
            continue
        bind.execute(forms.update().where(affected).values(status="cancelled"))
        bind.execute(
            events.insert().values(
                id=str(uuid4()),
                client_id=row.client_id,
                employee_id=row.employee_id,
                enrollment_id=row.id,
                kind="cancelled",
                reason="This enrolment was reset before employee reasons were recorded. "
                "Contact your benefits team for the reason.",
                email_unavailable_reason="Historical cancellation recovered from the audit trail; "
                "no retrospective email was sent.",
                created_at=row.cancelled_at,
                updated_at=row.cancelled_at,
            )
        )


def downgrade() -> None:
    raise RuntimeError("Enrolment history must be retained; use a forward migration.")
