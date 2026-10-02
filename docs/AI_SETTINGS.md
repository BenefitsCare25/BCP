# AI Settings

Implemented and deployed on 28 September 2026. This is the operator guide for AI Settings. See the [implementation record](AI_GOVERNANCE_IMPLEMENTATION_PLAN.md) for engineering details and the [UI specification](AI_GOVERNANCE_UX_DESIGN.md) for current screen behavior.

## Find the feature

Open **Settings → AI Settings** in the broker sidebar. This replaces **AI Provider** at the same `/settings/ai` route.

| Tab | URL | Purpose |
| --- | --- | --- |
| Provider & usage | `/settings/ai?tab=provider` | Shared provider and platform limits; selected-company overrides, budgets and usage |
| Policies | `/settings/ai?tab=policies` | Platform-wide documents shared across all firms and companies |
| AI use & access | `/settings/ai?tab=usage` | Existing role permissions and explanations of supported workflows |

- [Production AI Settings](https://inspro-portal.azurewebsites.net/settings/ai)
- [Production policy library](https://inspro-portal.azurewebsites.net/settings/ai?tab=policies)
- [Local policy library](http://127.0.0.1:5173/settings/ai?tab=policies), while the local frontend and API are running

The page opens without choosing a company. Company settings and usage require a selection in the top bar. Policies and role guidance show **Platform · All companies** and do not depend on that selection. Claim settings, review rules and decisions remain in **Claims Review**.

## Permissions

| Role | Provider & usage | Policies |
| --- | --- | --- |
| `system_admin` | Manage shared credentials, platform limits and company overrides | Upload drafts, download, publish, add versions and archive |
| `broker_admin` | Manage the selected company's key and budget | Read/download published versions and previously published archived versions |
| `broker_viewer` | Read available usage | Same published-document access; no modifications |

The backend enforces these permissions. Client/HR and member portal identities do not receive policy-library access. There is no separate governance-owner assignment, policy-approval role or company-specific policy library.

## Publish and maintain a policy

1. As a `system_admin`, open **Policies → Upload policy**.
2. Enter a title and type: **AI usage policy**, **Data handling**, **Operating procedure** or **Other**. Add an optional review date.
3. Choose a PDF up to 10 MB and select **Save draft**. Only system administrators can see or download this draft.
4. Download and check the saved PDF, then select **Publish** and confirm **Publish policy**. Broker users can now read it platform-wide.
5. For a revision, select **Add version**. Title and type carry forward and are locked; choose a revised PDF and its review date. The replacement starts as a draft. Publishing it archives the previous published version.
6. Use **Include archived versions** for retained history. **Version details** identifies the uploader, publisher, archiver and applicable dates. **Archive** removes a version from the current list while retaining the file.

A draft archived without publication stays hidden from broker roles. Previously published archived files remain readable. A passed review date displays **Review overdue**; it does not withdraw a policy, disable AI or send reminders. The list uses 50 versions per page.

Files cannot be edited in place or deleted through this feature. Correct a PDF by uploading another version. Publication records the administrator's action; it is not a separate multi-person approval workflow.

There are no seeded approved policies. Supply the organization's actual PDFs. Repository readiness and design documents are engineering documentation and are not automatically imported into the library.

## Storage and history

Metadata lives in shared PostgreSQL table **`public.ai_policy_versions`**, outside per-firm schemas. Files use the existing retained storage backend:

| Environment | File location |
| --- | --- |
| Production with Azure storage | Private Azure Blob storage under `platform/ai-policies/{policy_id}/{version_id}.pdf` |
| Default local storage | `backend/var/uploads/platform/ai-policies/{policy_id}/{version_id}.pdf`; `INSPRO_STORAGE_DIR` can override the root |

Downloads go through `/api/v1/ai-policies/{version_id}/download`, not a public blob link. The API checks permissions and the stored SHA-256, serves the PDF as an attachment and marks the response `private, no-store`. Storage paths are omitted from metadata responses.

Actor names and timestamps are retained with each version. Upload, publish and archive also use the existing audit log. Version history is not a WORM/immutable-storage guarantee or a substitute for backups. Recovery needs both database metadata and referenced blobs.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| No Upload policy button | Uploading requires `system_admin`; broker roles are readers |
| No published policies | Upload and publish a document; saving a draft alone does not publish it |
| A version is missing | Check archived versions, pagination and whether your role can see drafts |
| Upload rejected | Supply a PDF up to 10 MB and a non-empty title |
| Policy changed in another session | Refresh and review the latest version before retrying |
| File unavailable / integrity check failed | Have a system administrator investigate storage and metadata; a failed download is not an empty policy |
| Old sidebar label after deployment | Reload the application; the route remains `/settings/ai` |

## Release and boundaries

Initial deployment was verified at `53e3c509b4640b68b0e9dabdbb8e0875a6079a6c` in [deployment run 36397494851](https://github.com/BenefitsCare25/BCP/actions/runs/36397494851). CI, the private migration, exact portal/worker version checks and production readiness passed.

The DEV-only prototypes, sample stores, owner forms and synthetic release approvals are removed. Old oversight links redirect into AI Settings. The former claim-decision preview query opens existing Claims Review.

This release provides provider administration, policy-document storage/publication and role guidance. It does not establish ISO certification or legal conformity, complete organizational risk assessments or staff-training records, or implement the previous proposal's claim-decision/reconsideration changes. The [readiness assessment](archive/AI_COMPLIANCE_READINESS_2026-09-28.md) remains a dated baseline with a separate implementation update.
