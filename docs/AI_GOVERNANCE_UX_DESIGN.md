# AI Settings UI specification

As implemented and deployed on 28 September 2026. This replaces the earlier annotated concept screens. See the [operator guide](AI_SETTINGS.md) and [implementation record](AI_GOVERNANCE_IMPLEMENTATION_PLAN.md).

## Purpose and navigation

One **Settings → AI Settings** page contains provider administration, platform policy documents and existing-role guidance. Reuse the broker application's Inter typography, warm neutral ground, red primary actions, semantic status colors and shared controls. Do not add owner assignments or compliance scores.

| Tab | Search parameter | Context |
| --- | --- | --- |
| Provider & usage | `tab=provider` (default) | Company selector for company settings; shared controls for system administrators |
| Policies | `tab=policies` | Platform · All companies |
| AI use & access | `tab=usage` | Platform · All companies |

Tab selection updates the URL. The page opens without choosing a company. Users select a company for its provider settings/usage; system administrators can still use shared provider and limit controls without that selection. Policies and guidance remain reachable.

## Provider & usage

Preserve the existing provider components and backend. System administrators manage shared credentials/limits; broker administrators manage their company's override and budget. Broker viewers receive usage information without mutation controls. Shared provider and selected-company override remain visibly distinct.

Provider configuration does not publish a policy or approve a use. Policy publication does not enable, suspend or change AI. Claim rules and decisions remain in Claims Review.

## Policies

The header explains platform-wide scope. **Upload policy** is shown only to `system_admin`. **Include archived versions** controls retained history. Empty states explain the missing upload/publication; never present sample policies as approved records.

Each entry shows title, version, a text status (**Draft**, **Published**, **Archived**), type, filename and size. Expandable **Version details** identifies uploader/publisher/archiver and dates. An optional review date displays **Review overdue** for an overdue published version.

Readers receive **Download**. System administrators also receive **Publish** for drafts, **Add version**, and **Archive** for non-archived versions. Broker roles cannot see drafts or never-published archived versions; previously published archived files remain available. The API enforces this independently of the UI.

Wide rows use document, review-date and action columns; narrow layouts stack these fields. Tabs scroll if needed without page-level horizontal overflow. Status includes text rather than color alone. Previous/Next pagination shows 50 versions per page.

### Upload and transitions

**Upload policy** opens the shared sheet with title, type, optional review date and PDF input. Show the 10 MB PDF limit beside the input. **Save draft** submits; **Cancel** is explicitly non-submit. Validation and server errors are visible.

**Add version** uses the same sheet with inherited, locked title/type, a new PDF and an optional review date. Saving never publishes automatically.

**Publish** confirms the document/version, platform-wide audience and replacement of the current published version. **Archive** separately confirms removal from the current list while retaining history. Mutations show pending state, refresh the library on success and report errors. Query failures expose **Try again**; downloads show progress/failure feedback.

No review reminders, deletion, in-place PDF editing or separate approval workflow are implemented.

## AI use & access

Explain the existing `system_admin`, `broker_admin` and `broker_viewer` permissions. There are no sample owners, owner selectors, action assignments or invented approval roles. Actual document actions identify their actors in version details.

Describe claim review, member claim autofill, and document extraction/setup assistance. **Open Claims Review** leads to the existing workflow. This is guidance, not an editable use register, risk assessment or release workspace.

## Retired routes and review material

| Old route | Current destination |
| --- | --- |
| `/firm/ai-oversight` | `/settings/ai?tab=policies` |
| `/firm/ai-oversight/member-journey` | `/settings/ai?tab=usage` |
| `/platform/ai-oversight` | `/settings/ai?tab=provider` |
| `/platform/ai-oversight/releases/claim-review-v3` | `/settings/ai?tab=provider` |
| `/claims/review?preview=ai-governance` | Existing production Claims Review; no sample decision screen |

The top-bar oversight entry, sample stores, mock member journey, gallery images/PDF and gallery build/QA tools are retired. The obsolete review folder has been removed. Review the actual frontend.

## Verification

[Browser tests](../frontend/e2e/ai-settings.spec.ts) cover desktop/mobile navigation, entry without company selection, read-only broker access, administrator upload/publication, legacy redirects and main-region accessibility. [Backend tests](../backend/tests/test_ai_policies.py) cover permissions and file/version behavior. Local real-API checks also verified persistence after reload and byte-identical downloads.

The UI contains no approved policy text, certification badge, compliance percentage, supplier-retention guarantee or automatic legal assessment.
