# AI governance implementation plan

Status: proposed implementation and design handoff, 28 September 2026.
Basis: [readiness assessment](AI_COMPLIANCE_READINESS_2026-09-28.md).
Companion: [UI/UX specification](AI_GOVERNANCE_UX_DESIGN.md).

## Outcome and boundaries

Make every active AI use attributable to an owner, assessable against evidence, and subject to effective human oversight. The first release should improve real claim handling while establishing the records needed for an AI management system.

The owner confirmed no EU customers, use or affected population. Record that fact and a review trigger; EU-specific conformity workflows are outside the initial release. ISO certification remains a later organizational assessment, never a status inferred from software setup.

This document specifies future work. It does not approve policies, designate real officers, certify the platform or implement production changes. Mock records and names in designs are synthetic.

The initial users are:

- Platform operator: owns the shared AI services, validation and supplier evidence.
- Broker firm administrator: owns that firm's deployment, staff readiness and actions.
- Authorized claims assessor: checks evidence, explains outcomes and handles review requests.
- Member: understands AI assistance, corrects extracted information and requests reconsideration.

Reuse the broker's existing Inter typography, warm near-white ground, compact tables, semantic status colors and existing controls. Member changes inherit the current portal components. No platform-wide visual redesign is needed.

## Delivery sequence

Estimates below are planning ranges in engineering person-days, not delivery commitments. They assume the existing stack and exclude time for business adjudication, supplier responses and external audit. Re-estimate after technical design; one engineer may work sequentially while business owners prepare evidence in parallel.

| Milestone | Scope | Indicative effort | Exit evidence |
| --- | --- | --- | --- |
| M0: accountability | Appoint owner; define scope, policy, use inventory, initial risk/impact assessment and supplier evidence requests. | Business-led; start immediately | Approved scope/policy and named owners; known gaps recorded rather than marked complete. |
| M1: claim safeguards | Structured decision reasons, public/internal separation, disclosure, review provenance and a manual-review operating procedure. | 6–10 engineering days | No unexplained adverse decision through supported APIs; no internal note exposed; historical AI inputs/versions linked to the human decision. |
| M2: AI oversight and validation | Firm/platform oversight views, versioned evidence register, approved release manifests, held-out evaluation and model-change gates. | 10–16 engineering days | One use has a complete, reviewed evidence chain; unvalidated configurations cannot silently become approved releases. |
| M3: continuous operation | Structured reconsideration requests, sampled outcome monitoring, staff evidence, incidents/actions and scoped evidence exports. | 6–10 engineering days | One end-to-end reconsideration, one monitored review period and one corrective action demonstrated with synthetic/approved data. |
| M4: audit readiness | Independent internal audit, management review and closure of findings; optional external certification engagement. | Organizational schedule | Audit and management-review evidence assessed against the complete licensed standard. |

The first build should be M1, while M0 is completed by the accountable people. A dashboard does not replace those business decisions.

## M0: minimum governance pack

Use a controlled document repository initially. A versioned document and register are sufficient; M2 can index their metadata and approvals without building a general document editor.

| Record | Minimum content | Business decision required |
| --- | --- | --- |
| Scope and responsibility | Legal entity; systems covered; customers/regions; accountable sponsor; operational, engineering, claims and privacy owners. | Name the actual owners and authorize their responsibilities. |
| AI policy | Permitted uses; human decision boundaries; sensitive-data handling; model-change approval; incident escalation; review cadence. | Approve the policy and residual-risk authority. |
| AI use register | Stable ID; intended purpose; provider/model; affected people; data categories; owner; deployed release; manual alternative. | Confirm intended uses and forbidden extensions. |
| Risk/impact register | Concrete harm scenario; affected parties; impact and likelihood rationale; safeguards; remaining risk; owner; due date; evidence. | Accept remaining risk, or require treatment before use. |
| Data and supplier record | What leaves Inspro; where it goes; processing purpose; access; supplier retention/training terms; local retention; deletion and backup behavior. | Verify actual agreements and approve the data rules. |
| Control applicability | Applicable controls, rationale, implementation evidence and justified exclusions. | Approve the control selection after reviewing the licensed ISO standard. |

Seed the inventory from the inspected code: placement-slip extraction, benefit/eligibility rule assistance, roster/schema assistance, member claim intake and claim review. Underwriting administration is a boundary to verify, not automatically an AI risk-scoring feature.

Initial risk scenarios: wrong extracted amount; wrong benefit limit/rate; incorrect eligibility rule; unsupported AI comparison; prompt instructions hidden in a document; poorer performance on particular document formats/languages; excessive disclosure of medical data; assessor over-reliance; a model update invalidating thresholds; an incorrect decision without a useful remedy.

## M1: operational safeguards

### Decision explanations

Extend the existing Claims Review decision form and endpoint. Continue using the existing claim lifecycle and permissions.

| Action | Required explanation | Audience |
| --- | --- | --- |
| Approve the full supported amount without a material exception | No mandatory free-text essay; retain evidence and actor. | Existing member confirmation. |
| Approve less than the claimed amount in policy currency | Reason category, member explanation and relevant supporting reference. | Member explanation is visible to the member. |
| Reject | Reason category, member explanation and relevant supporting reference. | Member explanation is visible to the member. |
| Request information | Specific missing item and what the member should do next. | Member-visible; preserve existing required-note behavior. |
| Override a material AI/system concern or exceed an allowed amount/limit | Internal rationale and supporting reference in addition to any member explanation required above. | Broker-only rationale; never copied into the member thread. |

Use policy-currency values for reduction comparisons. Require a valid conversion before a comparison or decision; round using the existing decimal-money rules. Keep existing limit/visit acknowledgements. Distinguish an advisory AI flag from a hard business constraint; this change does not introduce a way around existing hard constraints.

Reason categories are proposed as: policy limit, excluded expense, eligibility/date issue, duplicate claim, missing evidence, calculation correction, insurer decision and other. “Other” needs specific text. An AI flag alone is not a sufficient member-facing reason. Templates may assist the assessor but must be reviewed before submission.

The server determines which fields are required. Support idempotency and revision checks; reject stale decisions with a recoverable conflict. Pin the exact AI review or an explicit “manual decision; no completed AI review” marker. Do not treat an unchecked box or a typed word as proof that an assessor inspected evidence.

Legacy decisions remain readable as “Explanation not recorded in the earlier workflow.” Do not fabricate historical reasons or backfill them from AI. Existing `decision_notes` remain member-visible according to their established behavior; add a separate field/record for internal rationale rather than silently changing that field's audience.

### Intake disclosure and manual fallback

Before the first document extraction request, show concise disclosure beside the upload control: AI reads the documents to suggest claim details; the member must check them; a person makes the claim decision. Provide a clear “Enter details myself” route.

That alternative bypasses AI autofill only. Required supporting documents remain required, and submitted documents may still undergo the disclosed AI-assisted review. Do not imply an AI-processing opt-out unless a complete no-AI workflow is deliberately implemented.

The disclosure is information, not a consent checkbox or a substitute for an appropriate processing basis. Link the relevant privacy information. Record the disclosure version without storing unnecessary device or personal data.

Preserve fallback when extraction fails. Never strand a claim because governance metadata is incomplete. A suspended or unavailable AI service must route claims to manual review; distinguish this from automatically permitting an unapproved model to run.

### Review provenance

Capture a manifest at enqueue time and maintain stage-level execution metadata. Include provider, exact available model identifier, prompt version/hash, configuration fingerprint, threshold-profile hash, application build ID, input-document references/hashes, claim revision and stage timestamps. Retain the versions actually used, including cache-hit provenance.

At execution, verify that the approved configuration still matches the manifest. Resume with the pinned compatible configuration or route to manual handling; never silently substitute a different model after a queue delay. Multiple stage calls may have separate metadata; a single last-seen model name is insufficient.

Keep the actual review result, reference the manifest from each decision, and retain authorized access to the evidence. This supports reconstruction; it does not promise that a nondeterministic external model can reproduce an identical output.

### M1 acceptance checks

- API and UI reject an unexplained rejection/reduction; whitespace alone does not pass.
- Internal rationale is absent from every member/HR serializer, notification, message and ordinary member export.
- Full approval without an exception remains fast; no unnecessary confirmation dialogue is added.
- A concurrent member amendment produces a conflict before saving the decision or notification; input is preserved for review.
- Double submission records one decision and one member notification.
- Manual decisions work during provider outage and identify that no completed AI review was used.
- AI disclosure precedes the extraction request for both single- and multiple-invoice paths.
- No live provider request is necessary for these behavior tests.

## M2: oversight workspace and approved releases

### Navigation and scope

| Surface | Proposed route | Scope and access |
| --- | --- | --- |
| Firm AI oversight | `/firm/ai-oversight` | New entry in the existing firm-wide top-bar tools. Broker administrator manages their own firm's records. Ordinary broker viewers see only a deliberately limited summary if authorized. |
| Platform AI oversight | `/platform/ai-oversight` | New system-admin-only Platform destination. Shared services, releases and supplier evidence. Explicit platform context, no selected-company chip. |
| Use detail | `.../uses/:id` within the appropriate scope | Stable link; register selection opens a detail panel on desktop, full page on narrow screens. |
| Release review | `/platform/ai-oversight/releases/:id` | Platform administrators prepare/approve platform releases. Firm owners review adoption and their own configurations. |
| Company AI provider | Existing `/settings/ai` | Credentials/connectivity remain here. Link to applicable release status; do not move or expose secret material. |

A broker firm's assurance records describe its deployment. Inspro's platform AIMS covers the platform operator. These are separate scopes even when one person administers both. Firm pages cannot enumerate other firms. A system administrator must explicitly choose the firm when accessing its records.

Existing `isCompanyPath` only recognizes `/home` and `/firm` as non-company paths. Implementation must add explicit platform context handling and route authorization together; adding a page alone would incorrectly inherit company selection. Update `nav.ts`, `AppShell`, `TopBar` and corresponding route tests as one change.

### Information architecture

Firm workspace: **AI uses / Actions / Evidence**. The use register is the landing page. Show the next unresolved issue and owner per use; avoid a percentage labeled “compliance.” Use details contain Purpose, Risks, Evidence and History. Staff competence and policy acknowledgements are evidence types with appropriate privacy restrictions, not public personnel scoreboards.

Platform workspace: **AI services / Releases / Data & suppliers / Actions**. Shared approved documents may be exposed to firms through explicitly published summaries. Raw contracts and platform security records remain restricted.

Separate lifecycle dimensions:

- Deployment: active, manual handling, suspended or retired.
- Evidence review: not assessed, action required, awaiting review, reviewed or review overdue.
- Policy/evidence version: draft, submitted, approved, superseded or expired.
- Release: draft, evaluation running, blocked, awaiting approval, approved, active, suspended or superseded.

“Reviewed” records a reviewer and date; it does not mean legally compliant or ISO certified. Suspending an AI use does not approve, reject, cancel or delete claims.

### Validation and release gate

Connection testing remains a distinct signal. An approved release requires a manifest and evaluation evidence matching the intended use, model, prompt, configuration and threshold profile.

Maintain development/calibration data separately from a protected held-out evaluation set. Approved anonymization and source access remain prerequisites. Record expected answers, actual outputs, adjudicator/reviewer identity, provenance and disagreement resolution. Do not publish identifiable medical documents into the general oversight workspace.

For claim review, measure incorrect clean results and unnecessary flags against independent adjudication. For intake, measure field accuracy, missing/conflicting information and correction burden. Report sample counts, denominators, coverage and uncertainty. Evaluate meaningful document/language/use groups where data collection is appropriate; do not infer protected traits to populate a dashboard.

The business risk owner approves acceptance criteria before evaluation. There is no universal sample size or invented legal accuracy threshold in this plan. Preserve the existing conservative production settings during transition until a justified replacement is approved.

Block activation when required evaluations are absent, criteria fail, the manifest changed, or the reviewer lacks authority. An approval attaches to a specific manifest; any material change invalidates that approval. Provide a controlled rollback to an earlier approved release or manual handling. A rollback also gets an audit record.

Use distinct preparer and approver identities for release approval. If team size cannot support this, record that organizational constraint and obtain an appropriate independent review arrangement; do not silently make self-approval the default. New BYOK model/configurations need the same functional assurance gate while preserving isolation of credentials.

### Proposed persistence and API boundaries

Names are implementation proposals, to be reconciled with repository conventions during build.

| Entity | Storage boundary | Key relationships |
| --- | --- | --- |
| Platform AI service/release | Public control tables | Approved manifest, evaluation reference, approval event; no member-level evidence. |
| Firm AI use/risk/action | Firm tenant schema | Firm deployment references a published service release; optional client scope limits company-specific records. |
| Evidence/version/approval | Separate scope-specific records | Owner, version, classification, digest, source link/object reference, review due date; restricted attachment access. |
| AI execution manifest | Firm tenant schema | Claim review and exact stage outputs; sensitive evidence remains claim-scoped. |
| Claim decision event | Firm tenant schema | Claim revision, review ID, member explanation, separate internal rationale, actor and notification event. |
| Review request | Firm tenant schema | Claim, original decision, member submission, assignee, resolution; separate from claim payment state. |

Prefer separate platform and firm routers with server-enforced scope. Tentative contracts: `/api/v1/platform-ai-oversight/*`, `/api/v1/firm-ai-oversight/*`, additive extensions to `/api/v1/claims/{id}/decision`, and `/api/v1/portal/claims/{id}/review-requests`. Never rely on a client-supplied `scope` field to grant global access.

Use additive migrations for existing and new firm schemas. Preserve old decisions, audit records and claims behavior. Restrict evidence attachments using existing file validation/storage patterns; protect downloads with authorization and audit. Generic evidence exports contain metadata and findings by default, not raw medical documents.

### M2 acceptance checks

- Cross-firm reads, writes, downloads and guessed IDs fail without revealing record existence.
- A firm administrator cannot mutate platform releases or view unpublished platform evidence.
- An unavailable evidence service displays an error; it never becomes “no outstanding actions.”
- Release approval and activation are separate actions; stale/different manifests cannot activate.
- Evaluation metrics show their population and denominator; zero observations render “Not measured.”
- Credential values never appear in use records, manifests, exports or screenshots.
- Evidence replacement creates a new version and preserves prior approvals; expired evidence cannot appear current.

## M3: review requests and operating evidence

A member can request reconsideration of a decision through the existing claim detail, with a clear explanation and optional supporting document. It creates a linked review request, not an automatic reversal, new claim, settlement cancellation or refund. Display the original outcome and “Review requested” separately.

Requests are idempotent; one open request per decision. Show its progress in the member's claim conversation. A claims assessor receives the request in an existing queue filter, reviews the original evidence and new material, and records an explained resolution. Use a different assessor where staffing permits. Preserve the original decision. Insurer-owned decisions follow the insurer escalation route; broker staff must not imply authority to reverse them.

Paid claims require the existing controlled amendment/settlement process if a resolution changes money. Do not directly change a paid claim to approved/rejected. Configure response expectations only after operations agrees a service target; do not invent a promised number of days.

Until structured requests ship, keep the existing member conversation route available and document how staff recognize, assign and track reconsideration requests manually. Do not advertise a dedicated feature before the backend workflow exists.

Introduce a monthly oversight review using sampled outcomes, overrides, member corrections/complaints, incidents and overdue actions. The output is an attributed review record and corrective actions. Monitoring should measure decisions as well as provider uptime. Link staff training/competence evidence and schedule refresh when use or risk changes.

M3 acceptance: member/household ownership checks; duplicate requests; rate limits and attachment safety; unsupported claim states; independent status from payment; explicit resolution; notification consistency; restricted operational notes; export and legal-hold behavior.

## Rollout and verification

1. Add schemas and backward-compatible reads; no fabricated historical approvals.
2. Add decision fields and the corresponding UI in a coordinated rollout. Update all known callers, including LOG/HR-origin broker workflows, before enforcing new required fields. Enforcement must apply consistently after cutover; do not leave a permanent legacy-client bypass.
3. Capture manifests for new reviews, mark historical provenance as unavailable, and verify the existing queue/recovery invariants.
4. Pilot the new oversight workspace with an explicitly selected firm and synthetic or authorized anonymized evidence.
5. Evaluate release gates in shadow mode, resolve unknown model mappings, then enforce activation gates. Existing releases require an explicit transition decision, not an automatic “approved” backfill.
6. Enable review requests after queue ownership and insurer/paid-claim routes are operational.

Verification should cover backend behavior and PostgreSQL tenancy/migrations, frontend type/build checks, and browser flows at desktop and mobile sizes. Test keyboard/focus behavior and accessible names. Use bounded visual review against the existing product. A full live provider accuracy evaluation is a separate controlled exercise, not an ordinary regression test.

Recommended success measures: every active use has an owner and reviewed purpose; every adverse new claim decision has a member explanation; every new AI review has a manifest or an explicit historical/unavailable marker; every activated new release has matching approved validation; every open corrective action has an owner; review requests are visible and assigned. Report counts and denominators rather than a synthetic compliance score.

## Decisions to resolve during implementation

These do not block preparing the designs. They must be supplied by accountable people before the related operational control is activated:

- Actual accountable owner, claims reviewer, privacy contact and release approver.
- Risk acceptance criteria and the representative validation population.
- Approved supplier terms, data retention schedules and legal-hold authority.
- Who may see firm evidence and individual competence records beyond firm administrators.
- Operational reconsideration target, insurer escalation route and money-adjustment authority.
- The legal entity/scope intended for any later certification.

## Code entry points

- Navigation/context: `frontend/src/components/shell/nav.ts`, `AppShell.tsx`, `TopBar.tsx`, `frontend/src/router.tsx`.
- Claim decisions: `frontend/src/routes/operations/claims.tsx`, `backend/app/api/v1/claims.py`, `backend/app/schemas/claims.py`.
- Member intake/detail: `frontend/src/components/portal/claims/AutofillCard.tsx`, `frontend/src/components/portal/leaf/ClaimDetailLeaf.tsx`.
- Review manifest/release checks: `backend/app/services/claims_review/queue.py`, `pipeline.py`, `backend/app/models/claim_ai_review.py`, `backend/app/core/ai_config.py`.
- Evaluation: `backend/app/services/claims_ai_evaluation.py`, `claims_ai_confidence.py`, `backend/evals/claims_ai/`.
- Existing audit, claim messaging, retained documents and tenancy helpers should remain the shared mechanisms.
