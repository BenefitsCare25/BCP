# AI Settings

Current implementation, 28 September 2026. This replaces the earlier governance prototype and its owner-assignment model.

## Find the feature

Open **Settings → AI Settings**, at `/settings/ai`.

- **Provider & usage** retains the existing shared provider, limits, company override and usage controls. Select a company in the top bar for company settings. A system administrator can configure the shared provider without selecting a company.
- **Policies**, at `/settings/ai?tab=policies`, is a platform-wide document library. No company selection is required.
- **AI use & access** explains the existing roles and application workflows. Operational claim settings and decisions remain in Claims Review.

## Permissions and documents

| Role | Provider setup | Policy documents |
| --- | --- | --- |
| `system_admin` | Shared provider, limits and company overrides | Upload, publish, add versions, archive and download |
| `broker_admin` | Selected company key and budget | Read and download published versions and previously published archived versions |
| `broker_viewer` | Read available usage | Same published-document access; no modifications |

Client portal roles have no access to this API. Permissions are enforced by the backend, as well as reflected by frontend controls.

Upload a PDF up to 10 MB, with a title, type and optional review date. Uploads begin as drafts visible only to system administrators. Publishing explicitly makes the version available to broker users. Publishing a replacement archives the previous current version. The **Include archived versions** option exposes retained history. Files are downloaded through an authenticated endpoint, with an integrity check and attachment disposition.

There are no seeded approved policies. A system administrator must supply the organization's documents. Earlier Markdown readiness and design handoffs are engineering documents, not approved policies and not automatically imported into the library. This feature does not itself establish ISO certification or legal compliance.

## Persistence and deployment

Metadata is stored in the shared `public.ai_policy_versions` table on PostgreSQL, deliberately excluded from per-firm tables. Migration `d8f2a4b6c0e1` adds the table and enforces one current published version per document.

PDFs use the existing retained storage backend under `platform/ai-policies/{policy_id}/{version_id}.pdf`: Azure Blob Storage in Azure mode, or `backend/var/uploads/` in default local mode. Documents are served by `/api/v1/ai-policies/{version_id}/download`; storage paths are not returned to browser clients. Actor names and timestamps are retained with each version. Upload/publish/archive operations also write to the existing audit log. Archiving retains files; this feature has no delete endpoint.

The previous DEV-only governance screens and synthetic stores have been removed. Old oversight URLs redirect into AI Settings. The existing production Claims Review continues to handle real decisions.

## Verification

- Backend policy tests cover role enforcement, draft visibility, version replacement, retained downloads, invalid uploads, storage cleanup and integrity failures.
- Browser tests cover desktop/mobile navigation, access without company selection, viewer permissions, upload/publish interactions and accessibility.
- The local integrated check exercised actual upload, publication, reload persistence, byte-identical download and archive against the running API.

Policy authoring, staff training records, risk assessments and organizational audit processes are separate work; this release does not imply those have been completed.
