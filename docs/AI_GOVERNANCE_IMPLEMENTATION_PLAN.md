# AI Settings implementation record and next steps

Updated 28 September 2026 to describe the deployed application. This replaces the earlier proposed multi-workspace governance plan. Start with the [operator guide](AI_SETTINGS.md) or [UI specification](AI_GOVERNANCE_UX_DESIGN.md).

## Delivered scope

| Area | Implemented behavior | Source |
| --- | --- | --- |
| Navigation | AI Provider renamed AI Settings; three tabs at the existing route | [Navigation](../frontend/src/components/shell/nav.ts), [router](../frontend/src/router.tsx) |
| Settings | Provider & usage, Policies, AI use & access; opens before company selection | [Page](../frontend/src/features/ai-settings/index.tsx) |
| Provider | Shared provider, limits, company overrides and usage preserved | [Provider page](../frontend/src/routes/configuration/ai-provider.tsx) |
| Policy UI | PDF draft upload, publication, downloads, replacement versions, archive and review dates | [Library](../frontend/src/features/ai-settings/policies.tsx) |
| Policy API | System administrator writes; broker roles read published history; portal roles denied | [API](../backend/app/api/v1/ai_policies.py) |
| Persistence | Shared metadata plus private files; one current published version per document | [Model](../backend/app/models/ai_policy.py), [migration](../backend/alembic/versions/d8f2a4b6c0e1_ai_policy_versions.py), [storage](../backend/app/core/storage.py) |
| Provisioning | Policies remain shared; schema-local index names prevent PostgreSQL truncation collisions | [Tenancy](../backend/app/db/tenancy.py) |

## API contract

Paths are relative to `/api/v1`. Existing authentication and write guards apply.

| Method and path | Access | Behavior |
| --- | --- | --- |
| `GET /ai-policies` | System administrator and broker roles | `{items, has_more}`; offset 0, limit 50 by default (maximum 100), `include_archived=false`; broker reads exclude never-published versions |
| `POST /ai-policies` | `system_admin` | Multipart `title`, `category`, optional `review_due`, optional `previous_id`, and `file`; creates a draft, returns 201 |
| `POST /ai-policies/{version_id}/publish` | `system_admin` | Publishes a draft and archives the current published version in its document group |
| `POST /ai-policies/{version_id}/archive` | `system_admin` | Archives while retaining file and actor history |
| `GET /ai-policies/{version_id}/download` | Role- and publication-scoped | Checks integrity and returns an authenticated PDF attachment |

Replacements inherit title/category. There is no update-in-place, deletion, owner-assignment, acknowledgement, reminder or separate approver endpoint. Version/publication uniqueness is enforced in the database; concurrent conflicts return a refresh/retry response.

## Release and verification

- `b65a3f1`: consolidated AI Settings, policy API/UI/storage and retirement of sample screens.
- `cb82aa7`: migration preserves the shared table when model-based provisioning created it first.
- `53e3c50`: short schema-local index names prevent the reproduced PostgreSQL naming collision during firm setup.
- [Deployment run 36397494851](https://github.com/BenefitsCare25/BCP/actions/runs/36397494851) passed backend, browser, static and container checks, applied the private migration, and verified the portal/worker release and database/Redis readiness.
- Local integration uploaded a synthetic PDF, published it, reloaded, downloaded identical bytes and archived the fixture. No approved organizational policy was imported.

Focused checks from the repository root:

```powershell
Push-Location backend
uv run pytest tests/test_ai_policies.py tests/test_ai_policy_migration.py tests/test_tenant_index_names.py tests/test_platform_ai_settings.py -q
uv run ruff check app tests scripts
uv run mypy app
Pop-Location
Push-Location frontend
pnpm test:e2e ai-settings.spec.ts
pnpm build
Pop-Location
```

CI additionally runs the full suites, including disposable PostgreSQL migration checks. Follow the [production runbook](PRODUCTION_RESILIENCE_RUNBOOK.md). If rolling application code back, preserve policy metadata and files: migration downgrade drops the version table and is not a routine rollback of retained documents.

## Practical next steps

1. **System administrator:** collect and approve actual usage policies, data-handling rules and procedures; upload/publish PDFs and choose review dates.
2. **Broker administrators:** check company provider/usage settings and read shared policies. Operational settings and human decisions stay in Claims Review.
3. **Organization:** retain supplier agreements, scope decisions, risk assessments, training and audit evidence through the relevant business processes. Application roles do not replace organizational responsibilities.
4. **Future engineering work, if prioritized:** reassess remaining model-validation, review-provenance and member-explanation gaps against the [dated baseline](AI_COMPLIANCE_READINESS_2026-09-28.md). These are not delivered capabilities or approved commitments in this release.

The owner-assignment model, separate oversight workspaces, synthetic release controls and member-journey preview are retired. Removed source and designs remain recoverable in Git history; they are not the plan for further implementation.
