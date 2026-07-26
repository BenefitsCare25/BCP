# Authentication & Sign-in Design — Inspro

Enterprise-grade, multi-tenant auth design for the Inspro Group Benefits platform.
Reconciles the greenfield "three-population auth" brief with what already ships in
this codebase, and specifies the **new** work (HR credential login + per-tenant
subdomain routing).

- **Stack**: FastAPI + SQLAlchemy + Alembic / React 18 + Vite / Postgres (Azure), SQLite (dev).
- **Jurisdiction**: Singapore. PDPA (all 11 obligations) + MAS TRM (insurance
  intermediaries are MAS-regulated) apply. See [§9 Compliance](#9-compliance).
- **Status**: DESIGN + GAP AUDIT. No code shipped by this document. Build sequence in [§11](#11-build-sequence).

Decisions locked with product (2026-07-24):

| Area | Decision |
|---|---|
| Broker staff auth | **Keep Microsoft Entra SSO.** No local passwords/Argon2id for brokers. Audit + harden only. |
| HR admin auth | **New credential login** — email *or* system-generated HR user ID + Argon2id + optional per-tenant TOTP. |
| Frontend delivery | **Three subdomains**, with **per-tenant subdomains** for HR + portal so a company's users can only reach their own tenant. |
| This deliverable | Design doc + gap audit first. Implement gaps after sign-off. |

Later refinement (post-build): the broker controls, **per company + per surface**,
which identifier is the login username (email / system-generated id / staff id) and
whether 2FA is available (self-enrol, optional). The employee portal moved from
email-OTP to the **same username + password** model as HR, so email-less employees
can sign in.

---

## Implementation status (2026-07-26)

Backend suite **1251 passing**; the ticked flows are browser-verified on
`{slug}.hr.localhost:5173` / `{slug}.portal.localhost:5173`.

| Area | Status |
|---|---|
| Tenant-per-subdomain (`clients.slug`, host parsing, middleware) | ✅ |
| Auth plumbing (Argon2id, TOTP, rotating sessions + reuse-revoke, `auth_events`, HIBP) | ✅ |
| Shared `core/mfa.py` (subject-agnostic TOTP) + `core/credentials.py` (lockout/version) | ✅ |
| HR credential login (email / HR-ID + password + optional TOTP) | ✅ verified |
| HR app shell (sign-in, set-password, dashboard, security/2FA enrol) | ✅ verified |
| Broker sign-in settings (per-company, per-surface: username source + 2FA on/off) | ✅ verified |
| Broker HR-account management (create / reset / regenerate-id / disable + policy) | ✅ verified |
| Employee portal → username + password (email / member ID / staff ID) + optional TOTP | ✅ verified |
| Portal sign-in + set-password (frontend) | ✅ verified |
| Broker member-credential UI (username, set-password link, direct set, regenerate-id) | ✅ verified |
| Member MFA enrol page + portal force-enrol nudge (`routes/portal/security.tsx`) | ✅ verified |
| Auth policy ENFORCEMENT (MFA on reset, forced rotation, idle timeout, breach on direct-set) | ✅ verified |

### Hardening pass (2026-07-26, post code-review)

Fifteen findings from the branch review, all fixed and covered by tests:

| Finding | Resolution |
|---|---|
| Email-OTP `/portal/auth/verify` minted a session with **no MFA / no forced-rotation check** — a member whose company required 2FA could skip it by choosing the emailed-code route | OTP is now the FIRST factor only: it ends in rotation challenge / MFA challenge / session, exactly like `/login` |
| Retained report versions were downloadable by `broker_viewer` **unmasked** (the live endpoints gate this; the create-time check is unreachable behind `require_write_access`) | `_assert_version_readable` wired into `/download` + `/movement`; single shared `assert_masking_allowed` |
| **No lockout on the second factor** — TOTP accepts a ±1 step window, so codes could be ground from rotating IPs | `is_locked` before / `register_failure` after on both surfaces (HR counters live on `AuthCredential`, not `User`) |
| A password reset revoked **nothing** — attacker sessions survived for the full absolute lifetime | `sessions.revoke_all_for_subject` on HR set-password + admin reset; member JWTs (stateless) carry a credential-version claim checked per request |
| `clients.slug` was **never populated** by any endpoint, script or migration, so every `{slug}.hr` / `{slug}.portal` host 404'd | `services/client_slug.py` + create/patch wiring + backfill migration `a3c5e7b9d1f4`; `slug` exposed on `ClientOut` |
| One-time recovery codes were destroyed by `refetchOnWindowFocus` when the user alt-tabbed to save them | Codes hoisted to page level so they survive the `enrolled` flip |
| MFA inputs stripped non-digits at `maxLength=6`, so a **recovery code could never be entered** — a lost phone meant permanent lockout | Shared `lib/mfa.ts`; the four sign-in / set-password forms accept both |
| HR client skipped refresh for all `/hr/auth/*`, but the authenticated calls live there too — 10-min idle logged admins out despite a valid refresh cookie | Exclusion narrowed to the genuinely public endpoints |
| Set-password links were bare paths (unclickable in email; token shown once) | `tenantSurfaceUrl()` builds absolute `{slug}.hr|portal.<base>` URLs from the new slug |
| Insurer-listing dedup/staleness keyed on the membership manifest only, missing ~33 rendered columns + underwriting/salary values — a retained submission record could silently diverge | Always fingerprints the rendered bytes; `is_stale` errs toward "stale" |
| AI concurrency limiter blocked while holding a pooled DB connection, so enabling it exhausted the pool | Bounded wait + releases the connection when the session is clean (it cannot roll back blindly — the review pipeline commits once at the end); new `AICapacityError` |
| `platform_ai_usage` row lock was held to the caller's commit, serialising every AI spend platform-wide behind one claim review | Own short transaction on Postgres (inline on SQLite, where a second connection deadlocks) |
| `/home` company rows were mouse-only, so keyboard users could not reach any workspace | Shared keyboard-operable `TableRow`; verified Tab → Enter |
| `ai-provider` fired a broker-only endpoint before the role resolved (403 toast on reload); sign-in swallowed 423/429; `list_versions` treated `scope_key=None` as "all scopes" | Fixed |
| Dead Bedrock tooling (`verify_bedrock.py` imported a removed dependency), stale provider message in `seed_claims_demo` | Removed / corrected |

**Residual:** the MFA challenge token stays replayable for its 5-minute TTL —
lockout now bounds grinding; true single-use needs a `jti` store.

**Human-only, you apply (per the §9 compliance split):**
1. Wildcard DNS + wildcard TLS + Front Door routing for `*.hr.<domain>` /
   `*.portal.<domain>`. Client slugs are now auto-assigned (and backfilled), but
   verify them before DNS goes live — the slug fronts the subdomain. Prod secret
   plumbing reuses the existing Bicep/Key-Vault pattern.
2. Set `VITE_TENANT_BASE_DOMAIN` on the frontend build so copied set-password
   links resolve to the tenant host rather than the broker origin.

**Notes for the reviewer:**
- New env: `INSPRO_BASE_DOMAIN` (default `inspro.sg`), `VITE_TENANT_BASE_DOMAIN`
  (frontend). New deps: `argon2-cffi` (backend), `qrcode.react` (frontend).
  Migrations head: `a3c5e7b9d1f4` (chain: `d1f3a5c7e9b2` → `e2b4d6f8a0c1` →
  `f4c6e8a0b2d3` → `a3c5e7b9d1f4`); verified apply-from-scratch on a clean DB.
- The **dev DB is `create_all`-built** (not migration-tracked) so its
  `alembic_version` is stale — a clean rebuild-from-migrations is advisable but
  independent of this feature.
- Email-OTP portal endpoints are retained under the hood; the portal UI now uses
  password login. TOTP secrets are Fernet-encrypted; only refresh-token hashes are
  stored; identifiers in `auth_events` are hashed.

---

## 1. What already exists (verified in code)

Inspro is **not** greenfield. Three auth surfaces already ship, two of them
production-grade. The brief's password-based scheme would be a *regression* if
applied to the surfaces that already use a stronger mechanism.

| Population | Mechanism (today) | Identity store | Token | Tenant isolation |
|---|---|---|---|---|
| **Broker staff** | Entra ID (Azure AD) OIDC, RS256 — **no local password** | `users` (matched by Entra `oid`/email) | Entra RS256 JWT, validated vs JWKS (`core/entra.py`) | `client_id` scope + Postgres schema-per-firm |
| **Employee (member)** | Email **OTP**, passwordless | `member_accounts` + `member_otp_codes` (hashed) | HS256 member JWT, `typ:"member"` (`core/portal_auth.py`) | Token hard-pins one `client_id`; `resolve_member_employee` scopes to the member's own Employee row |
| **HR admin** | *No distinct login.* Roles `client_admin` / `client_hr` exist in the `Role` literal but currently ride the broker Entra surface | `users` + `user_client_access` grants | — | `user_client_access` grants + firm schema |

Already-built primitives this design **reuses, not rebuilds**:

- **Fail-closed auth modes** — `settings.py` refuses to boot with `mock`/unset auth in prod.
- **Cryptographic surface separation** — broker RS256(Entra) vs member HS256 tokens never cross-validate.
- **Tenant boundary** — every tenant-owned resource routed through `core/deps.py`
  (`load_policy_year`, `assert_policy_year_for_user`, …); cross-tenant → **404 not 403**;
  `system_admin` cross-tenant access flagged in `AuditLog.cross_tenant_access`.
- **Physical isolation (Postgres)** — `firm_<id>` schema per broker firm; control tables
  (`users`, `clients`, `member_accounts`, …) in `public`; `set_search_path` per request.
- **DB-backed identity** — roles/tenant binding from the `users` table, never token claims.
- **Encrypted-at-rest secret store** — Fernet (`core/crypto.py`, `INSPRO_AI_KEY_ENCRYPTION_KEY`)
  already used for BYOK AI keys. **Reused here** for TOTP secrets.
- **Rate limiter** — SlowAPI-style `INSPRO_RATE_LIMIT_*` buckets already in place.

**Design principle: additive.** The new HR credential surface plugs into the same
`CurrentUser`/RBAC/tenant machinery. It does not fork identity.

---

## 2. Target architecture — three subdomains, tenant-per-subdomain

```
                         ┌───────────────────────────── inspro.sg (apex → marketing) ───┐
                         │                                                               │
  broker.inspro.sg ──────┼─ Broker app (Entra SSO)          firm resolved from users row│
                         │                                                               │
  {slug}.hr.inspro.sg ───┼─ HR app  (credential login)      tenant = clients.slug       │
                         │      *.hr.inspro.sg  (wildcard DNS + wildcard TLS)            │
                         │                                                               │
  {slug}.portal.inspro.sg┼─ Employee portal (OTP)           tenant = clients.slug       │
                         │      *.portal.inspro.sg (wildcard DNS + wildcard TLS)         │
                         └───────────────────────────────────────────────────────────────┘
```

### Why subdomain = tenant (the key move)

The brief asks how login "disambiguates which tenant + role an identifier belongs
to." **The host header answers it before a credential is ever entered.**

- `acme.portal.inspro.sg` → `clients.slug = "acme"` → `client_id` + `broker_firm_id`
  resolved by middleware **pre-auth**. The login form only ever authenticates
  *within that tenant*.
- An employee ID / HR ID entered on `acme.*` is looked up **scoped to Acme's tenant**.
  The same identifier at `beta.*` is a different account. No global namespace, no
  cross-tenant guesswork, no tenant-selector step.
- **Cookie isolation is structural.** Session cookies are set **host-only** (no
  `Domain` attribute) so `acme.portal.inspro.sg`'s cookie is physically never sent
  to `beta.portal.inspro.sg`. This is the isolation property product asked for:
  *"other employees can only access their own company's portal."*

> **Anti-pattern to avoid:** setting `Domain=.inspro.sg` on session cookies. That
> shares the cookie across every tenant subdomain and collapses the isolation.
> Cookies MUST be host-only.

### Tradeoffs vs a single role-aware app

| | Three subdomains + wildcard (chosen) | Single app, role-aware routes |
|---|---|---|
| Tenant isolation | Strong — DNS + host-only cookies + origin separation | UI-layer only; relies on token discipline |
| Tenant resolution | Free (host header) | Needs selector / identifier-namespace lookup |
| CSRF blast radius | Per-origin | Shared origin |
| Infra cost | Wildcard DNS `*.portal`/`*.hr`, wildcard TLS, Front Door routing, `clients.slug` | None new |
| Ops | Onboarding a client provisions a slug; cert auto-covers | Nothing |

Given the brief's explicit isolation requirement and MAS TRM zoning expectations,
the subdomain model is the right call. Broker stays a **single** host (`broker.`)
because broker staff are firm-scoped, not client-scoped — their firm comes from the
`users` row, and `X-Inspro-Client` continues to select the active client in-app.

### Azure shape (residency-safe)

- Region: **Southeast Asia (Singapore)** only. Storage **LRS/ZRS**, never GRS
  (GRS replicates to Hong Kong — breaks SG residency).
- Front Door / App Gateway routes `*.portal.inspro.sg` + `*.hr.inspro.sg` + `broker.`
  to the same App Service; the app reads the `Host` header for tenant.
- **Wildcard TLS** cert (`*.portal.inspro.sg`, `*.hr.inspro.sg`) in Key Vault, managed identity.
- No new deployment secret for tenant routing; slug lives in the DB.

---

## 3. Identity & tenancy model

### 3.1 Collision handling — email is unique *per tenant*, not globally

The same human email can legitimately be an employee at two client companies, or an
HR admin at one and an employee at another. Rules:

- **Members**: already correct — `UniqueConstraint(client_id, email)` and
  `(client_id, staff_id)` on `member_accounts`. Email is tenant-scoped. Keep.
- **HR admins**: HR credential identity is scoped to the tenant reached by the
  subdomain. A given `(client, email)` and `(client, hr_login_id)` are unique; the
  same email under another client is a separate credential. (Broker `users.email`
  stays globally unique because broker staff are firm-internal, not client-facing.)
- **Cross-surface**: broker (Entra), HR (credential), member (OTP) are separate
  principal types with separate tokens. An email existing in all three is three
  independent identities — never silently merged. This is already true broker↔member;
  HR joins the same pattern.

### 3.2 Identifier uniqueness is `(tenant, identifier)`

- Employee ID (`member_accounts.staff_id`) — **already** `UniqueConstraint(client_id, staff_id)`. Correct.
- HR user ID (`hr_login_id`, new) — **system-generated, opaque, non-guessable**
  (e.g. `HR-7Q2M8K`), unique within the broker firm; the subdomain narrows to the client.
- **NRIC MUST NOT be an identifier.** From **31 Dec 2026** NRIC is banned for
  authentication in private orgs (PDPC). Employee IDs and HR IDs are synthetic. If a
  roster carries NRIC it stays a *data attribute* (masked, `national_id_normalized`),
  never a login credential. This is a hard design constraint — see [§9](#9-compliance).

### 3.3 Roles (RBAC vocabulary — extend, don't rename)

Existing `Role` literal (`core/auth.py`): `broker_admin`, `broker_viewer`,
`client_admin`, `client_hr`, `system_admin`. Members are a **separate principal**
(`CurrentMember`), not a role. Mapping to the brief's populations:

| Brief population | Inspro role(s) | Surface |
|---|---|---|
| Broker Platform (Inspro staff) | `system_admin`, `broker_admin`, `broker_viewer` | `broker.` (Entra) |
| HR Platform (client company HR) | `client_admin`, `client_hr` | `{slug}.hr.` (credential) |
| Employee Portal | *(member principal, no role)* | `{slug}.portal.` (OTP) |

No renames needed. The gap is the **login surface** for `client_admin`/`client_hr`,
not the authz vocabulary.

---

## 4. Database schema

### 4.1 Reused as-is

`broker_firms`, `clients`, `users`, `user_client_access`, `invitations`,
`member_accounts`, `member_otp_codes`, `audit_log`. No changes to their columns
except the additions below.

### 4.2 New / altered tables

All new control-plane tables live in **`public`** (auth resolves *before* a firm
schema is known) and are added to `CONTROL_TABLES`. JSON columns use
`json_variant()`; timestamps use `sa.func.now()`; String PKs via `new_uuid` — per
repo migration conventions.

**(a) `clients.slug` — subdomain segment**

```
ALTER clients ADD COLUMN slug String(63)  UNIQUE, NULLABLE  -- DNS label: [a-z0-9-], ≤63
ALTER clients ADD COLUMN portal_enabled  Boolean  default true
ALTER clients ADD COLUMN hr_enabled      Boolean  default true
```
- `slug` is the tenant key for `*.portal`/`*.hr`. Nullable during migration; required
  before a tenant's subdomains go live. Validated against DNS-label rules + a reserved
  list (`www`, `api`, `admin`, `broker`, …).

**(b) `auth_credentials` — local password for credential-login principals (HR today)**

Keyed to a `users` row. Entra users have **no** row here → cannot local-login
(fail-closed). Kept separate from `users` so the Entra surface stays password-free
and the table generalizes to any future local surface (e.g. broker break-glass).

```
auth_credentials
  id                 PK
  user_id            FK users.id  UNIQUE  (one credential per user)
  hr_login_id        String(32)  NULL     -- system-generated HR ID; UNIQUE(broker_firm_id, hr_login_id)
  broker_firm_id     FK broker_firms.id   -- denormalized for the login_id uniqueness scope
  password_hash      String(255)          -- Argon2id PHC string (algo+params embedded)
  password_updated_at DateTime
  must_rotate_after  DateTime  NULL        -- per-tenant forced-rotation policy; NULL = no forced rotation
  failed_attempts    Integer  default 0
  locked_until       DateTime  NULL        -- per-identifier lockout (backoff)
  last_login_at      DateTime  NULL
  UNIQUE(broker_firm_id, hr_login_id)
```

**(c) `auth_mfa` — TOTP enrolment (surface-agnostic)**

```
auth_mfa
  id                 PK
  subject_type       String(16)   -- "user" | "member"
  subject_id         String(36)   -- users.id or member_accounts.id
  totp_secret_enc    Text         -- Fernet-encrypted (core/crypto.py); NEVER plaintext
  confirmed_at       DateTime NULL -- enrolment not trusted until first valid code
  recovery_codes     JSON         -- list of Argon2id/SHA-256 hashes; single-use
  last_used_step     Integer NULL  -- replay guard: reject reuse of the same 30s step
  UNIQUE(subject_type, subject_id)
```
- TOTP is **mandatory** where policy requires (Entra enforces broker MFA at the IdP,
  so `auth_mfa` is not used for brokers); **per-tenant configurable** for HR and members
  via `client_auth_policy` below.

**(d) `auth_sessions` — rotating refresh-token family**

```
auth_sessions
  id                 PK
  subject_type       String(16)   -- "user" | "member"
  subject_id         String(36)
  client_id          FK clients.id NULL  -- active tenant this session is pinned to
  broker_firm_id     String(36) NULL
  family_id          String(36)   index  -- rotation lineage; reuse of a rotated token revokes the family
  refresh_hash       String(64)   -- SHA-256 of the refresh token; raw token only in the cookie
  parent_id          String(36) NULL      -- previous session in the rotation chain
  issued_at          DateTime
  expires_at         DateTime
  rotated_at         DateTime NULL
  revoked_at         DateTime NULL
  ip                 String(45)
  user_agent         String(255)
  subdomain          String(255)  -- host the session was minted on (isolation audit)
```
- **Reuse detection**: presenting a refresh token whose row is already `rotated_at`
  (i.e. a stolen/replayed old token) → revoke the entire `family_id`. Standard
  refresh-rotation defense.

**(e) `auth_events` — structured, tenant-tagged auth audit (PDPA-retained)**

Deliberately **separate** from `audit_log` (which is mutation/entity oriented with
`before`/`after`). Auth events are high-volume, may have no resolved subject (failed
login), and carry a different retention policy.

```
auth_events
  id                 PK
  occurred_at        DateTime  index
  event_type         String(48) index  -- login_success | login_fail | mfa_challenge | mfa_fail
                                        -- | password_reset_request | password_reset_complete
                                        -- | lockout | token_refresh | token_reuse_detected | logout
  outcome            String(16)         -- success | fail | blocked
  surface            String(16)         -- broker | hr | portal
  subject_type       String(16) NULL    -- user | member (NULL if unresolved failed login)
  subject_id         String(36) NULL
  client_id          FK clients.id NULL index   -- tenant tag
  broker_firm_id     String(36) NULL
  identifier_hash    String(64) NULL    -- hash of the attempted email/ID (never store raw on failure)
  ip                 String(45)
  user_agent         String(255)
  subdomain          String(255)
  detail             JSON NULL          -- reason codes, MFA method, breach-list hit, etc.
```
- **PDPA retention**: append-only; exported to immutable storage (Azure immutable
  blob) so a compromised app cannot erase its own trail. Enough fields to determine
  breach *scope within days* (PDPC ≤3-day notification, ≥500-individual threshold).

**(f) `client_auth_policy` — per-tenant auth policy (config, not identity)**

```
client_auth_policy
  client_id          PK/FK clients.id
  mfa_required        Boolean default false   -- force TOTP for this tenant's HR+members
  password_min_entropy Integer default 60     -- bits (zxcvbn-scored), HR only
  password_rotation_days Integer NULL          -- NULL = no forced rotation
  session_idle_minutes Integer default 30      -- MAS TRM idle timeout
  session_absolute_hours Integer default 12
  breach_check_enabled Boolean default true    -- HIBP k-anonymity on set/reset
```

### 4.3 Entity-relationship (control plane, `public`)

```
broker_firms 1──* clients ──1 client_auth_policy
     │               │  1
     │               └──* member_accounts 1──* member_otp_codes
     │                          │
users *──1 broker_firm          └── auth_mfa(subject=member)
  │  1
  ├── auth_credentials(1)        auth_sessions(subject=user|member)
  ├── auth_mfa(subject=user)     auth_events (tenant-tagged, append-only)
  └── user_client_access *──1 clients
```

---

## 5. Login flows (incl. identifier→tenant resolution)

Every request first passes **tenant-resolution middleware**:

```
resolve_tenant(request):
  host = request.host                       # e.g. "acme.portal.inspro.sg"
  if host == "broker.inspro.sg":  surface = BROKER; tenant = None (from user row later)
  elif host matches "{slug}.hr.inspro.sg":     surface = HR;     tenant = clients.by_slug(slug)
  elif host matches "{slug}.portal.inspro.sg": surface = PORTAL; tenant = clients.by_slug(slug)
  else: 404                                  # unknown slug → generic 404, no tenant leak
  if tenant and (disabled or wrong surface flag): 404
  stash (surface, tenant) on request.state
```

### 5.1 Broker staff — `broker.inspro.sg` (unchanged)

```
1. GET broker.inspro.sg → redirect to Entra (MSAL, existing frontend/src/auth/msal.ts)
2. Entra authenticates + enforces MFA (phishing-resistant for admins — MAS TRM)
3. Callback → RS256 access token → API Authorization: Bearer
4. _entra_principal(): verify vs JWKS → match users row by oid/email → CurrentUser
5. X-Inspro-Client header selects active client among firm's clients
```
No tenant slug — firm comes from `users.broker_firm_id`. **No change beyond hardening.**

### 5.2 HR admin — `{slug}.hr.inspro.sg` (NEW)

```
1. Middleware resolves tenant from slug (pre-auth).
2. POST /hr/auth/login { identifier, password }
     identifier = email OR hr_login_id (frontend detects format; server accepts both)
3. Resolve credential WITHIN the tenant:
     - locate users row: role in {client_admin, client_hr}, granted to this client
       (user_client_access), matched by email OR auth_credentials.hr_login_id
     - constant-time compare Argon2id password_hash  (dummy-hash path if no user, to
       avoid user-enumeration timing)
4. Gates (each writes auth_events):
     - locked_until in future → 423 Locked
     - password verify fail → failed_attempts++, exponential backoff, maybe lockout
     - breach/rotation policy → force reset if must_rotate_after passed
5. If MFA enrolled or client_auth_policy.mfa_required:
     - 200 { mfa_required: true, challenge_id }
     - POST /hr/auth/mfa { challenge_id, totp } → verify auth_mfa (replay-guard step)
6. Issue session:
     - short-lived access JWT (HS256, typ:"hr", 10 min) in memory (returned in body)
     - rotating refresh token → auth_sessions family; set as httpOnly Secure
       SameSite=Strict cookie, HOST-ONLY (no Domain), Path=/api
7. Every API call: access token → CurrentUser(role=client_admin/hr, client_id=tenant).
   Tenant in token MUST equal the subdomain's tenant (defense vs token replay across slugs).
```

### 5.3 Employee — `{slug}.portal.inspro.sg` (OTP, mostly unchanged)

```
1. Middleware resolves tenant from slug.
2. POST /portal/auth/request-code { email }   (scoped to tenant → member_accounts(client_id,email))
     - always 200 (anti-enumeration); dev+mock returns debug_code
     - OTP hashed (keyed HMAC, existing), 10-min TTL, ≤5 attempts
3. POST /portal/auth/verify { email, code } → member HS256 JWT (existing issue_member_token)
4. (New, optional) if client_auth_policy.mfa_required for portal → TOTP step before token
5. Token → CurrentMember, hard-pinned client_id; resolve_member_employee scopes to own Employee row
```
Change vs today: the client is now resolved from the **subdomain** rather than being
implicit in the account lookup — reinforcing that a member on `acme.portal` can only
ever touch Acme. Optional per-tenant TOTP is additive.

---

## 6. Auth mechanics

| Control | Design | Applies to |
|---|---|---|
| **Password hashing** | **Argon2id**, `argon2-cffi`. Params: `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`, 16-byte salt, 32-byte tag (OWASP 2024 floor). PHC string stored; params upgradable on next login. | HR only |
| **Password policy** | ≥12 chars, entropy ≥ `password_min_entropy` bits (zxcvbn), **HIBP k-anonymity** breach check on set/reset (send 5-char SHA-1 prefix only), per-tenant rotation. | HR only |
| **Access token** | Short-lived JWT (10 min). HR = HS256 `typ:"hr"`; member = HS256 `typ:"member"` (existing); broker = Entra RS256. Distinct `typ` + key per surface — no cross-validation. **Not** in localStorage — returned in body, held in memory. | all |
| **Refresh token** | Opaque random (32 bytes), rotating, in **httpOnly + Secure + SameSite=Strict + host-only** cookie, `Path=/api`. Reuse of a rotated token revokes the family. | HR, member |
| **Session timeout** | Idle `session_idle_minutes` (default 30), absolute `session_absolute_hours` (default 12) — MAS TRM. | HR, member |
| **Rate limit / lockout** | Per-IP via existing SlowAPI buckets (`INSPRO_RATE_LIMIT_*`); per-identifier via `auth_credentials.failed_attempts` + exponential `locked_until`. OTP: existing ≤5 attempts + TTL. | all |
| **MFA (TOTP)** | RFC 6238, 30s step, ±1 window, secret Fernet-encrypted, single-use recovery codes, replay guard (`last_used_step`). Broker MFA is enforced by **Entra** (not `auth_mfa`). | HR + member (per policy); broker via Entra |
| **Transit** | TLS 1.3 (1.2 floor), HSTS. Wildcard cert in Key Vault. | all |
| **Audit** | Every auth event → `auth_events`, tenant-tagged, append-only, exported to immutable storage. | all |

---

## 7. Authorization (server-side, per request)

Unchanged core, extended to the HR principal:

- **RBAC enforced server-side on every route**, never route-guard-only. The existing
  `core/deps.py` gates (`load_policy_year`, `assert_policy_year_for_user`,
  `require_client_id`, `can_write_global`) already do this for `CurrentUser`. The HR
  credential login produces the **same `CurrentUser`** (role `client_admin`/`client_hr`),
  so it inherits every existing gate for free.
- **Tenant embedded + validated per request.** Broker: `X-Inspro-Client` validated in
  `resolve_active_client_id`. HR/member: `client_id` is baked into the token AND must
  equal the subdomain's tenant (`request.state.tenant`). Mismatch → 404. Cross-tenant
  resource access remains **404, not 403** (`_deny_cross_tenant`, security-logged).
- **IDOR defense** is the existing JOINed-load-or-404 pattern — no new surface bypasses it.

---

## 8. Module sketches (design, not final code)

### 8.1 FastAPI

```
app/core/
  tenancy_host.py        # resolve_tenant middleware: Host → (surface, client_id) on request.state
  hr_auth.py             # get_current_hr_user(); Argon2id verify; login_id resolution
  passwords.py           # hash_password / verify_password (argon2-cffi); needs_rehash
  breach_check.py        # HIBP k-anonymity client (prefix-only, cached, fail-open logged)
  totp.py                # enrol / verify / recovery codes (Fernet secret via core/crypto)
  sessions.py            # issue/rotate/revoke refresh family; reuse detection
  auth_events.py         # write_auth_event(...) — the single audit sink

app/api/v1/
  hr_auth.py             # POST /hr/auth/{login,mfa,refresh,logout,password/reset-request,password/reset}
  portal_auth.py         # existing OTP + optional /portal/auth/mfa
  hr_admin.py            # broker/admin: provision HR user, (re)generate hr_login_id, set policy

# main.py: register hr_auth OUTSIDE require_write_access (like portal_auth);
#          add resolve_tenant middleware ahead of auth deps.
```

Key dependency contract (mirrors `get_current_user` / `get_current_member`):

```python
def get_current_hr_user(
    authorization: str | None = Header(None),
    request: Request = ...,          # request.state.tenant from middleware
    db: Session = Depends(get_db),
) -> CurrentUser:
    # 1. decode HS256 typ:"hr" access token
    # 2. load users row; assert role in {client_admin, client_hr}, status active
    # 3. assert token.client_id == request.state.tenant.client_id   (else 404)
    # 4. set_search_path(db, broker_firm_id); return CurrentUser(...)
```

### 8.2 React

Three build targets (or one app, three Vite entry HTMLs by host — either works;
recommend **separate entry points, shared component library**):

```
frontend/src/
  auth/
    IdentifierField.tsx    # detects email vs HR-ID (regex), inline validation feedback
    LoginForm.tsx          # shared shell; surface prop swaps copy/branding
    useAuthSession.ts      # in-memory access token; silent /refresh on 401 (broker: MSAL)
  surfaces/
    broker/    → broker.inspro.sg   (existing MSAL flow)
    hr/        → *.hr.inspro.sg     (credential form + TOTP step)
    portal/    → *.portal.inspro.sg (existing OTP; reuse AuthScene shell)
```
- **Identifier-type detection**: `email` if it matches an email regex, else treat as
  HR-ID (`^HR-[A-Z0-9]{6}$`); show inline hint. Server still accepts both — client
  detection is UX only, never the security boundary.
- Reuse the existing clean-white `AuthScene` shell (per `project_auth_signin_scene`
  memory) across all three for a shared-but-distinct look.
- Tokens **never** in localStorage; access token in memory, refresh in the httpOnly cookie.

---

## 9. Compliance

### PDPA (always applies — personal data throughout)

| Touchpoint | Obligation | Design response |
|---|---|---|
| First HR/member login | Notification + consent for data collected | Show privacy notice on first sign-in; record consent event in `auth_events.detail`. |
| Password-reset / OTP email | Purpose limitation; delivery security | Fail-closed mail mode in prod (existing `_resolve_mail_mode`); reset links single-use, short TTL. |
| Auth logs | Breach scope determination ≤3 days; ≥500 individuals = notifiable | `auth_events` retains enough to scope a breach; immutable export. |
| **NRIC** | **Banned for auth (private orgs) from 31 Dec 2026**; avoid collection | **Login IDs are synthetic, never NRIC.** NRIC stays a masked attribute only. |
| Retention | Document + enforce | `auth_events` retention policy defined per PDPA; sessions expire + purge. |
| Transfers | Comparable-protection (s26) | SG-only region; LRS/ZRS storage — no HK replication. |

### MAS TRM (insurance intermediary — MAS-regulated)

| Area | Expectation | Design response |
|---|---|---|
| Access control | Strong auth, MFA | Entra MFA (broker, phishing-resistant for admins); TOTP for HR/member per policy. |
| Session mgmt | Timeout limits | Idle 30 min + absolute 12 h (`client_auth_policy`). |
| Audit trail | Tamper-evident, retained | `auth_events` append-only + immutable blob export. |
| Cryptography | TLS 1.2 floor / 1.3; encrypt at rest | TLS 1.3; TOTP secrets Fernet-encrypted; CMK for regulated stores. |
| Resilience | RTO/incident notice (Cyber Hygiene: 1 h) | Stateless tokens + DB sessions ease failover; alerting on lockout/reuse spikes. |
| Testing | Pen-test expectation | Annual pen-test + adversarial CI on the auth surface. |

### AI-configurable vs human-only (from `sg-compliance-setup` §5)

**Human-only, never in an AI session**: Entra tenant + Global Admin, break-glass FIDO2
ceremony, any credential *value* (Argon2id pepper, portal JWT secret, Fernet master key —
all via Key Vault references), production IaC apply, DPO appointment + breach filings.
**AI-configurable (reviewed before apply)**: all the auth *code*, Bicep for DNS/TLS/Front
Door, Conditional Access policy-as-code, `client_auth_policy` defaults, tests.

---

## 10. Security review summary

**Mitigated by this design**

- Cross-tenant access / IDOR — subdomain tenant binding + host-only cookies + existing
  JOINed-load-or-404 + token↔subdomain equality check.
- Credential stuffing / brute force — Argon2id, per-identifier lockout, per-IP rate limit,
  HIBP breach check, MFA.
- Token theft / replay — short access TTL, rotating refresh with family reuse-revocation,
  httpOnly+Secure+SameSite=Strict host-only cookies, no localStorage.
- User enumeration — constant-time verify with dummy-hash path; anti-enumeration OTP responses;
  failed-login identifiers stored hashed only.
- Session fixation / CSRF — rotation on auth, SameSite=Strict, per-origin subdomains.
- Offline hash cracking of OTP/TOTP tables — keyed HMAC (OTP) + Fernet-encrypted TOTP secrets.
- Auth-trail tampering — append-only `auth_events` exported to immutable storage.
- Cross-surface token confusion — distinct `typ` + signing key per surface; Entra RS256 vs HS256.

**Explicitly out of scope (first pass)**

- WebAuthn/passkeys for HR/member (TOTP first; passkeys a later hardening step).
- Broker local/break-glass password path (Entra-only for now; `auth_credentials` table
  is built to accept it later without schema change).
- SСIM / automated HR-admin deprovisioning from client HRIS.
- Device fingerprinting / risk-based step-up beyond MAS baseline (Entra Conditional Access
  covers broker; HR/member risk-scoring deferred).
- Fraud/velocity analytics on OTP requests beyond rate limits.
- DDoS at the edge (delegated to Azure Front Door / WAF, not app-layer).

---

## 11. Build sequence

1. **Foundations** — `clients.slug` (+ validation, reserved list), `resolve_tenant`
   middleware, wildcard DNS/TLS + Front Door routing (Bicep — human applies).
2. **Auth plumbing** — `passwords.py` (Argon2id), `sessions.py` (rotation + reuse
   detection), `auth_events.py`, `auth_mfa`/`totp.py`, `client_auth_policy`. Migrations.
3. **HR credential login** — `hr_auth.py` routes + `get_current_hr_user` dep + broker-side
   provisioning (`hr_login_id` generation, policy). Tenant-isolation tests
   (`test_tenant_isolation.py`) for every new endpoint.
4. **Portal hardening** — subdomain tenant binding on OTP flow; optional per-tenant TOTP.
5. **Frontend** — HR surface (`*.hr.`) + `IdentifierField`; portal subdomain wiring;
   shared `AuthScene`. Broker unchanged.
6. **Compliance wiring** — immutable `auth_events` export, HSTS/TLS policy, first-login
   consent capture, NRIC-as-auth lint guard.
7. **Verification** — auth unit/isolation tests, pen-test pass, breach-scope drill.

Each backend step ships with `test_tenant_isolation.py` coverage (the isolation
harness fails any new tenant endpoint that skips the dep) — per repo convention.
```
