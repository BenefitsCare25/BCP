"""drop legacy azure_foundry BYOK configs

Azure Foundry was removed as a provider (chore/remove-azure-foundry). Any
``client_ai_configs`` row still carrying ``provider='azure_foundry'`` is now
dead data: no code path can build a client for it (``_load_byok`` returns None),
and ``GET /ai-config`` 500s trying to validate the value against the narrowed
``AIProviderStr`` literal. The upsert endpoint validates provider against that
literal, so no new such rows can appear — this one-time delete clears the
stragglers. Affected tenants fall back to env AI and can reconfigure with a
supported provider (Vertex).

Revision ID: b7d1e2f4a9c3
Revises: d5c9e1f3a7b8
Create Date: 2026-07-23
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "b7d1e2f4a9c3"
down_revision = "d5c9e1f3a7b8"
branch_labels = None
depends_on = None


def _delete_client_ai_configs(where: str) -> None:
    """``client_ai_configs`` is a per-firm-schema TENANT table on Postgres, so a
    bare DELETE (search_path = public) misses the real rows in the ``firm_<id>``
    schemas. Run the delete in public AND every firm schema. On SQLite there are
    no schemas, so one unqualified delete covers everything.
    """
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        bind.execute(text(f"DELETE FROM client_ai_configs WHERE {where}"))
        return
    schemas = [
        row[0]
        for row in bind.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'public' OR schema_name ~ '^firm_'"
            )
        )
    ]
    for schema in schemas:
        # An older firm schema may predate client_ai_configs; skip if absent.
        present = bind.execute(
            text("SELECT to_regclass(:qualified)"),
            {"qualified": f'"{schema}".client_ai_configs'},
        ).scalar()
        if present is None:
            continue
        bind.execute(text(f'DELETE FROM "{schema}".client_ai_configs WHERE {where}'))


def upgrade() -> None:
    _delete_client_ai_configs("provider = 'azure_foundry'")


def downgrade() -> None:
    # The deleted rows held encrypted keys for a removed provider; there is
    # nothing to restore.
    pass
