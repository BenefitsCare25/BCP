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

revision = "f2a4c6e8b0d3"
down_revision = "b7d1e2f4a9c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM client_ai_configs WHERE provider IN ('bedrock', 'anthropic')"
    )


def downgrade() -> None:
    # The deleted rows held encrypted keys for removed providers; nothing to
    # restore.
    pass
