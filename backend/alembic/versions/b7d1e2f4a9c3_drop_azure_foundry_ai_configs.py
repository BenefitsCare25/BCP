"""drop legacy azure_foundry BYOK configs

Azure Foundry was removed as a provider (chore/remove-azure-foundry). Any
``client_ai_configs`` row still carrying ``provider='azure_foundry'`` is now
dead data: no code path can build a client for it (``_load_byok`` returns None),
and ``GET /ai-config`` 500s trying to validate the value against the narrowed
``AIProviderStr`` literal. The upsert endpoint validates provider against that
literal, so no new such rows can appear — this one-time delete clears the
stragglers. Affected tenants fall back to env AI and can reconfigure with a
supported provider (bedrock / vertex / anthropic).

Revision ID: b7d1e2f4a9c3
Revises: d5c9e1f3a7b8
Create Date: 2026-07-23
"""
from __future__ import annotations

from alembic import op

revision = "b7d1e2f4a9c3"
down_revision = "d5c9e1f3a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM client_ai_configs WHERE provider = 'azure_foundry'")


def downgrade() -> None:
    # The deleted rows held encrypted keys for a removed provider; there is
    # nothing to restore.
    pass
