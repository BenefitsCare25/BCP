# Employee Self-Service Portal

Members (insured employees of a client company) sign in to view their own
insurance benefits and flex wallet, manage dependants, and submit claims —
replacing the legacy portal that the IVM scraper platform reads from.

Full build plan: `~/.claude-work/plans/swift-splashing-wind.md` (4 phases).

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Member identity, email-OTP auth, read-only benefits, broker provisioning | ✅ Shipped 2026-07-04 |
| 2 | Retained document storage, claim submission, dependant self-add + broker approval | ✅ Shipped 2026-07-04 |
| 3 | AI claim review pipeline (IVM-modeled) + broker review queue | ✅ Shipped 2026-07-05 |
| 4 | Utilization (per-benefit limits + flex wallet depletion) | ✅ Shipped 2026-07-05 |

## Architecture

### A separate auth surface — NOT a broker role

Members never touch Entra and never appear in the `users` table. Adding an
"employee" role to the broker `Role` literal would have granted broker-level
write access through the global `require_write_access` gate (it only blocks
`broker_viewer`), so the portal is a parallel principal type:

```
Broker surface                          Member surface
──────────────                          ──────────────
Entra RS256 JWT (JWKS-verified)         HS256 JWT, typ:"member"
                                        (INSPRO_PORTAL_JWT_SECRET)
users table (oid/email match)           member_accounts table (email OTP)
get_current_user                        get_current_member (core/portal_auth.py)
X-Inspro-Client picks active tenant     hard-pinned to ONE client via token
require_write_access router loop        portal routers registered separately
```

Neither surface's tokens verify on the other (different algorithms + keys) —
covered by `tests/test_portal_isolation.py`.

### Member identity model

- **`member_accounts`** (control table in `public`, listed in `CONTROL_TABLES`):
  the stable cross-policy-year login identity. Unique `(client_id, email)` and
  `(client_id, staff_id)`. Status: `invited → active` (first successful OTP
  verify) `| disabled`.
- **`member_otp_codes`**: HMAC-SHA256-hashed one-time codes, 10-min TTL,
  consumed on success or after 5 failed attempts.
- **`employees.member_account_id`**: per-policy-year binding, stamped at
  provisioning. When a new policy year's roster arrives without the stamp,
  `resolve_member_employee` falls back to a `(policy_year_id, staff_id)` match
  and stamps it; an ambiguous staff_id is a 409 (broker must fix the roster).

### Member scoping rule

**Every portal endpoint resolves data through
`resolve_member_employee(db, member)`** — the member's own Employee row in the
client's *active* policy year. Portal handlers never accept a client/employee
id from the request and never filter by bare `client_id` (which would expose
co-workers). Cross-member access is impossible by construction; regression
coverage lives in `tests/test_portal_isolation.py`.

### What members see (and don't)

`services/member_statement.py` wraps the broker benefit statement and strips:
- `financials` (per-member premium / sum-insured figures)
- matching internals (`match_method`, `match_confidence`, `rule_human_readable`)

Everything else (SOB, plan codes, dependant coverage, flex wallet) is shared
with the broker view — the frontend renders both from the same components.

## API surface

### Public (own abuse guards, no auth)

| Endpoint | Behaviour |
|----------|-----------|
| `POST /api/v1/portal/auth/request-code` | Always 202 (no account enumeration). 5/min per IP + 60s per-account cooldown + 5/hour cap. Dev+mock returns `debug_code`. |
| `POST /api/v1/portal/auth/verify` | 10/min. 5 attempts burn the code. First verify activates an `invited` account. Returns `{token, expires_at, member}`. |

Magic link format: `{frontend_origin}/portal/sign-in?email=…&code=…` (same
verify endpoint; `INSPRO_FRONTEND_ORIGIN` sets the origin).

### Member (Bearer member token)

| Endpoint | Behaviour |
|----------|-----------|
| `GET /api/v1/portal/me` | Profile + active policy year + `flex_eligible`. Never 404s (the shell needs it). |
| `GET /api/v1/portal/benefit-statement` | Member-safe statement. 404 "No active coverage" when no active year / no roster row. |
| `GET /api/v1/portal/dependants` | Own dependants only (all statuses, incl. pending). |
| `POST /api/v1/portal/dependants` | Self-add → `pending_approval` (10/min). `POST …/{id}/documents` attaches proof (pending only, 20/min). |
| `GET /api/v1/portal/coverage-options` | Products + SOB benefit items + claimable flex categories + covered dependants — drives the claim-form pickers. |
| `GET/POST /api/v1/portal/claims` | List own claims / create draft (20/min). `GET /{id}` detail; `DELETE /{id}` draft only. |
| `POST /api/v1/portal/claims/{id}/documents` | Attach receipt (pdf/png/jpg, 15 MB, 20/min; draft/needs_info only). |
| `POST /api/v1/portal/claims/{id}/submit` | 10/min. Validates: active policy year, incurred date in-period, ≥1 receipt, claimed coverage exists on the member's own statement, duplicate receipt SHA-256 → structured 409 `duplicate_receipt`. |
| `GET /api/v1/portal/utilization` | Own claim usage vs limits (computed on read; see "Utilization" below). |

### Broker provisioning (normal gated surface)

| Endpoint | Behaviour |
|----------|-----------|
| `GET /api/v1/member-accounts` | Active client's accounts. |
| `POST /api/v1/employees/{id}/member-account` | Invite (20/min). Email from roster `EMAIL_KEYS`, overridable in body. 409 on duplicate email/staff_id. |
| `POST /api/v1/member-accounts/{id}/resend-invite` | 10/min. |
| `PATCH /api/v1/member-accounts/{id}` | `{status: active\|disabled}`. |
| `POST /api/v1/member-accounts/bulk-invite` | 10/min. Whole policy year; skips existing accounts and rows without a roster email. |

All provisioning is audited (`member_account.*` actions); portal-originated
mutations use `write_member_audit` (`actor_type="member"`).

### Broker claims review

| Endpoint | Behaviour |
|----------|-----------|
| `GET /api/v1/claims?policy_year_id=&status=&employee_id=` | Review list with staff_id/name + latest AI review summary (`ai_review`). |
| `GET /api/v1/claims/{id}` | Detail incl. documents. `GET …/documents/{doc_id}/download` streams the receipt. |
| `GET /api/v1/claims/{id}/review` | Latest non-superseded AI review (extractions, field comparisons, rule results, vision checks, tokens/cost). 404 when none. |
| `POST /api/v1/claims/{id}/rerun-review` | 10/min. Supersedes the current review + re-queues the pipeline (claim → `ai_review_pending`). |
| `POST /api/v1/claims/{id}/decision` | `{action: approve\|reject\|needs_info, note, approved_amount?, acknowledge?}`. Approve defaults to the claimed (or converted) amount; approving beyond the bucket's remaining limit → 409 `limit_exceeded` unless `acknowledge=true`. `needs_info` reopens the claim for the member. Illegal transitions → 409 `invalid_transition`. |
| `GET /api/v1/employees/{id}/utilization` | Broker view of one employee's claim usage vs limits (same service as the portal endpoint). |
| `POST /api/v1/dependants/{id}/approval` | `{action: approve\|reject}`. Approve activates + re-runs flex assignment (same path as roster upload); pending-only (409 otherwise). |
| `GET /api/v1/dependants/{id}/documents` (+ `/download`) | Member-attached proof documents. |
| `GET /api/v1/dependants?status=pending_approval` | The approval queue filter. |

### Claim status machine

```
draft → submitted → ai_review_pending → ai_verified | ai_flagged
broker decision (from submitted/ai_*/needs_info): approve | reject | needs_info
broker rerun-review (from submitted/ai_verified/ai_flagged) → ai_review_pending
needs_info → member adds docs + resubmits → submitted
pipeline fault → claim returns to submitted (manual review); review row = error
```
Single source of truth: `VALID_TRANSITIONS` in `app/models/claim.py`. Members
see AI-internal states only as "Under review" — fraud signals are never
surfaced to members. Pending dependants never appear in the benefit statement
or family-status/flex-wallet resolution until approved.

### AI review pipeline (Phase 3)

Submit sets `ai_review_pending`, inserts a pending `claim_ai_reviews` row, and
dispatches `claims_review.pipeline.run_review` via FastAPI BackgroundTasks
(the task opens its own session and re-establishes the firm `search_path`).
Stages, modeled on the IVM pipeline:

1. **Deterministic pre-checks** (`claims_review/rules.py`, zero AI tokens):
   in-period date, receipt SHA-256 reuse across live claims, amount vs annual
   limit / flex balance, non-SGD currency warning. Any hard fail →
   `ai_flagged` immediately.
2. **Extraction** — one `ai_gateway.extract_claim_document` call per document
   (vision; PDFs go as native document blocks). Cache key = document SHA-256,
   so a resubmitted receipt never re-extracts.
3. **Comparison** — `ai_gateway.review_claim` compares the member's
   `form_fields` snapshot against extracted fields using the in-code field
   maps (`claims_review/field_maps.py`), judges AI business rules, and checks
   required document families for the claim type.
4. **Selective vision verify** — up to 4 `ai_gateway.verify_claim_concern`
   calls on MISMATCH/UNCERTAIN vision-eligible fields; CONFIRMED flips the
   comparison to MATCH.
5. **Verdict** (`claims_review/verdict.py`) — `clean` → `ai_verified`, else
   `flagged` → `ai_flagged`. Any exception degrades to manual review (claim
   back to `submitted`, review row records the error) — the member is never
   blocked by a pipeline fault.

All three gateway entries ride `_run_cached_ai_call` (cache/breaker/budget/
spend); spend operations: `ai_claim_extract`, `ai_claim_review`,
`ai_claim_vision_verify`. Broker queue UI: `/operations/claims` (status tabs,
AI verdict badges, review panel, decision dialogs, rerun).

### Utilization (Phase 4)

`app/services/utilization.py` — computed on read, no persisted counters.
Buckets keyed `(product_code, benefit_key)`:

- `approved` = Σ `amount_approved` of approved claims; `pending` = Σ in-flight
  claim amounts, shown separately and **never** subtracted from remaining
  (a pending claim may be rejected); `remaining` = limit − approved.
- Product-level limits parse from `annual_policy_limit`; a benefit item's SOB
  value only counts as an annual limit when it isn't per-unit ("S$650/day",
  "80%", "As charged" → no limit). Claims against coverage no longer on the
  statement surface as `orphaned` buckets.
- Flex chain: tier wallet → minus enrollment price-tags (`flex_balance`) →
  minus approved flex claims → `available`; claimable categories track their
  `sub_limit`s.
- **Approve guard**: the decision endpoint computes `remaining_for_claim`
  (the tightest applicable bucket — product limit, benefit item limit, flex
  available, or category sub-limit) and 409s `limit_exceeded` when the
  approving amount exceeds it; the broker resends with `acknowledge=true` to
  override (the queue UI re-arms the dialog as "Approve anyway").

UI: members see `/portal/utilization` ("My usage"); brokers see the same bars
under the statement on `/operations/benefit-statement`
(`components/benefits/UtilizationView.tsx` — approved solid, pending hatched).

### Retained document storage

`app/core/storage.py` — the ONLY place upload bytes survive (`saved_upload`
elsewhere still discards). `LocalStorage` at `backend/var/uploads/`
(gitignored, PII) in dev; `AzureBlobStorage` in prod (`INSPRO_STORAGE_MODE=azure`,
managed identity via `INSPRO_STORAGE_ACCOUNT_URL`, shared-key access disabled).
SHA-256 is computed at write and stored on `stored_documents.sha256` — the
duplicate-receipt / tampering signal. Paths are namespaced
`{firm}/{client}/{entity_type}/{entity_id}/{doc_id}{suffix}` and confined to
the storage root. Bicep provisions the storage account + `documents` container
+ Blob Data Contributor role; the deploy workflow passes
`PORTAL_JWT_SECRET_{STAGING,PROD}` GitHub secrets into Key Vault.

## Configuration

| Env var | Default | Notes |
|---------|---------|-------|
| `INSPRO_PORTAL_JWT_SECRET` | ephemeral + WARNING (dev/staging) | **Required in prod** (fail-closed, ≥32 chars). |
| `INSPRO_PORTAL_TOKEN_TTL_HOURS` | `12` | Member token lifetime. |
| `INSPRO_MAIL_MODE` | `log` (dev/staging) | `log` (codes in backend log) \| `smtp` (`INSPRO_SMTP_HOST/PORT/USER/PASSWORD/FROM`) \| `acs` (stub). **Fail-closed in prod**: must be `smtp` or `acs` — `log` would write sign-in codes to application logs in cleartext. Broker invite/resend responses carry `mail_sent` so delivery failures are visible. |
| `INSPRO_FRONTEND_ORIGIN` | `http://localhost:5173` | Magic-link origin. |

## Frontend

`/portal/*` is a sibling route tree in `src/router.tsx` with its own guard
(`hasValidPortalSession`) — the broker MSAL guard never runs for it.

- `src/stores/portalSession.ts` — persisted member token (`inspro-portal-session`).
- `src/api/portalClient.ts` — portal fetch wrapper: member bearer token, **no**
  `X-Inspro-Client`, 401 → clear session + `/portal/sign-in`.
- `src/api/portal.ts` — hooks (`["portal", …]` query keys).
- `src/components/portal/PortalShell.tsx` — slim member shell (no client
  switcher / policy-year picker; the member is pinned server-side).
- Pages: `src/routes/portal/sign-in.tsx` (username/email + PASSWORD, optional
  TOTP — not OTP; see the auth note below), `coverage.tsx`, `claims/`,
  `clinics.tsx`, `enrollment.tsx`, `cards.tsx`, `messages.tsx`.
- Broker side: `MemberAccountActions` renders inside the employee detail panel
  of the **Coverage & Members** page — sidebar *Policy Admin → Coverage &
  Members*, route **`/operations/coverage`**, Broker view, scrolled past the
  product coverage blocks to the **"Portal access"** section. It is NOT on
  `/operations/employees`: that path is a legacy redirect to
  `/operations/roster?tab=employees` (see `router.tsx`), and the roster page
  has no portal-access panel. The component file is still named
  `routes/operations/employees.tsx`, which is what makes this easy to get
  wrong — trust `router.tsx`, not the filename.

## Trying it locally — end-to-end walkthrough (all 4 phases)

Setup: `cd backend && ./scripts/dev.ps1` and `cd frontend && pnpm dev`, then
open http://localhost:5173. For Phase 3's live AI review you also need an AI
provider configured — Google **Vertex AI (Gemini)** is the only provider now
(AWS Bedrock and direct Anthropic were removed): a platform or per-company
service-account key on `/configuration/ai-provider`, or `VERTEX_PROJECT` +
Google ADC for local dev. Without one the pipeline degrades gracefully
(see step 3c).

**Shortcut — seed everything at once:**

```sh
cd backend && PYTHONPATH=. uv run python scripts/seed_claims_demo.py
```

Idempotent; seeds the Demo client with an ACTIVE policy year (2026-01-02 →
2026-12-31), resolved GHS coverage (S$2,000 annual limit + per-item limits),
a confirmed Flex scheme (Tier 1, S$1,000 wallet, Dental/Optical sub-limits),
two portal members, a pending dependant, generated PDF receipts, and 9 claims
covering every state — including a canned flagged AI review (renders without
any AI key), an errored review (manual-review fallback), and two claims sized
to trip the `limit_exceeded` approve guard. Then: broker UI → switch to the
**Demo client** + the **2027** policy year; portal → sign in as
`demo.member@inspro.test` (data everywhere) or `demo.colleague@inspro.test`
(empty — member isolation). With the seed in place you can skip straight to
whichever phase you want to test; the steps below still describe the manual
path from scratch.

### Phase 1 — sign-in + benefits view

> **Sign-in is PASSWORD-based (+ optional TOTP), not an emailed OTP.** The OTP
> endpoints (`/portal/auth/request-code`) still exist server-side but nothing
> links to them from the sign-in screen. Anything below describing a code is
> historical.

1. The member needs the year to be the **current** one — set it from the
   banner on `/configuration` (the old `/operations/activations` page is gone;
   nothing promotes a year automatically, so a fully configured company reads
   as "no active coverage" until you do this).
2. Go to **Policy Admin → Coverage & Members** (`/operations/coverage`), stay
   in **Broker view**, pick the employee, and scroll the detail panel to
   **"Portal access"**. Create the account there if the badge says "No portal
   account", then use either:
   - **Set-password link** — a one-time link (72h) you hand over; works
     without email, and resolves to the right host in single-host header mode.
   - **Set password** — set one directly (email-less members).
   "Invite all to portal" bulk-invites the year, but note that **an invite
   needs working email**, which prod does not currently have (`docs/EMAIL_SETUP.md`).
3. Open `/portal/sign-in?company=<slug>` and sign in with the username (system
   login id) or email, plus that password. The `?company=` is required in
   header-tenancy mode (local dev and prod both) — without it the tenant can't
   be resolved and every attempt reads as "Those details weren't recognised".
4. You land on "My benefits": SOB and flex wallet, **no premium figures**
   (paste the portal token into a broker API call to confirm it 401s).

### Phase 2 — dependant self-add + claim submission

1. Portal → "My dependants" → add a dependant (name, relationship, DOB,
   optional proof upload). It appears with a **Pending approval** badge and
   does NOT appear on "My benefits" dependant coverage yet.
2. Broker → `/operations/dependants` → the "Pending dependant approvals"
   card → approve. Back in the portal, the dependant is active and (for a flex-
   eligible member) the family-status tier/wallet re-resolves.
3. Portal → "My claims" → "New claim": pick a coverage line (insured product +
   benefit item, or a claimable flex category), fill amount/date/provider,
   attach ≥1 receipt (pdf/png/jpg, ≤15 MB), submit.
   - Negative checks worth doing once: submit with no receipt (422), an
     incurred date outside the policy year (422), and re-uploading the same
     receipt file on a second claim (409 `duplicate_receipt`).
4. The member sees the claim as **"Under review"** — AI-internal states are
   never exposed to members.

### Phase 3 — AI review pipeline + broker queue

1. The submit from Phase 2 dispatched the pipeline in the background. Watch
   the backend log for the stages (extraction → review → vision verify).
2. Broker → `/operations/claims`: the claim lands under **Verified** (clean)
   or **Flagged**; open it — the sheet shows the AI review panel (field
   comparisons with MATCH/MISMATCH badges, system + AI rule checks, vision
   re-checks, token/cost footer) and receipt download links.
   - Verify caching: "Re-run AI review" completes near-instantly and spends
     ~0 new tokens (extraction is cached on the receipt's SHA-256); the old
     review row is superseded. Spend rows land in the AI spend log as
     `ai_claim_extract` / `ai_claim_review` / `ai_claim_vision_verify`.
3. Degradation paths to confirm:
   a. Submit a flex claim larger than the member's flex balance (submit only
      validates coverage, not amounts) — the deterministic pre-check fails
      and flags the claim with **zero** AI tokens spent (summary says
      "Flagged by deterministic checks").
   b. "Request more info" → the member sees the note, adds a document,
      resubmits → a fresh review run starts.
   c. With NO AI provider configured, submit a claim: the review row records
      the error and the claim falls back to **Manual review** in the queue —
      the member is never blocked.
4. Decide the claim (approve / reject / needs info) from the sheet; the
   member's view updates to Approved/Rejected/"More info needed".

### Phase 4 — utilization + limit guard

1. Portal → "My usage": per-product bars (approved solid, pending hatched)
   with remaining amounts, per-benefit sub-rows, and the flex chain
   (wallet → price tags → balance → claims → available).
2. Broker → `/operations/benefit-statement` → select the member: the same
   "Claims utilization" panel renders under the statement. Approve a claim in
   the queue and refresh — approved moves out of pending, remaining drops.
3. Limit guard: submit + approve claims until a bucket's remaining is small,
   then approve another claim exceeding it — the approve dialog shows the
   limit warning and re-arms as **"Approve anyway"** (409 `limit_exceeded`
   under the hood; confirming again resends with `acknowledge=true`).
   Approving a partial amount within the remaining needs no acknowledgement.

## Tests

- `tests/test_portal_auth.py` — OTP lifecycle: happy path, cooldown, hourly
  cap, lockout, expiry, single-use, enumeration-safe 202, disabled accounts,
  activation-on-verify, token typ/expiry gating.
- `tests/test_portal_isolation.py` — member-level isolation (own row only,
  staff-id fallback stamping, ambiguity 409), cross-surface token rejection
  (incl. entra-mode), financials-gating unit test.
- `tests/test_tenant_isolation.py` — member-account endpoints 404 cross-tenant.
- `tests/test_claims_review_pipeline.py` — pipeline stages, short-circuits,
  vision cap, degradation, rerun supersession, submit dispatch.
- `tests/test_ai_gateway_claims.py` — claim gateway cache keys, breaker
  semantics, spend rows.
- `tests/test_utilization.py` — bucket math, grouping, flex chain, zero
  baseline, the limit-exceeded guard (+ acknowledge), member isolation.

## Phase 2+ (see the plan file for full detail)

- **Claims**: `claims` + `stored_documents` tenant tables; draft → submitted →
  ai_review_pending → ai_verified/ai_flagged → approved/rejected/needs_info
  state machine; submission gated to the active policy year period; duplicate
  receipts detected by SHA-256; documents RETAINED via `app/core/storage.py`
  (local dir in dev — `backend/var/`, gitignored, PII — Azure Blob in prod).
- **Dependant self-add**: portal-created dependants start `pending_approval`
  with optional proof docs; broker approval activates them + re-runs flex
  assignment.
- **AI review** (Phase 3, shipped): see "AI review pipeline" above —
  `claim_ai_reviews` tenant table, `app/services/claims_review/` package,
  three `ai_gateway` entries, broker queue at `/operations/claims`.
- **Utilization** (Phase 4, shipped): see "Utilization" above —
  `services/utilization.py`, member + broker endpoints, the `limit_exceeded`
  approve guard, and the shared `UtilizationView` bars.
