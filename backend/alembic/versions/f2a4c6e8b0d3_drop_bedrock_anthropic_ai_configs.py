"""drop legacy bedrock/anthropic BYOK configs

Vertex/Gemini is now the sole AI provider (AWS Bedrock and direct Anthropic were
removed). Any ``client_ai_configs`` row still carrying ``provider`` in
('bedrock', 'anthropic') is dead data: ``_load_byok`` returns None for it and
``GET /ai-config`` would 500 validating the value against the narrowed
``AIProviderStr`` literal. The upsert endpoint only writes ``provider='vertex'``,
so no new such rows can appear — this one-time delete clears stragglers.
Affected tenants fall back to env AI and reconfigure with a Vertex key.

Revision ID: f2a4c6e8b0d3
Revises: b7d1e2f4a9c3
Create Date: 2026-07-23
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "f2a4c6e8b0d3"
down_revision = "b7d1e2f4a9c3"
branch_labels = None
depends_on = None


def _delete_client_ai_configs(where: str) -> None:
    """(Same rationale as b7d1e2f4a9c3.) ``client_ai_configs`` is a
    per-firm-schema tenant table on Postgres, so delete in public AND every
    ``firm_<id>`` schema; one unqualified delete on SQLite.
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
        present = bind.execute(
            text("SELECT to_regclass(:qualified)"),
            {"qualified": f'"{schema}".client_ai_configs'},
        ).scalar()
        if present is None:
            continue
        bind.execute(text(f'DELETE FROM "{schema}".client_ai_configs WHERE {where}'))


def upgrade() -> None:
    _delete_client_ai_configs("provider IN ('bedrock', 'anthropic')")


def downgrade() -> None:
    # The deleted rows held encrypted keys for removed providers; nothing to
    # restore.
    pass
