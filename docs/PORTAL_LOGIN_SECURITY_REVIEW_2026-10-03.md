# Employee and HR login review, 3 October 2026

Scope: both sign-in pages, their API clients, session stores, authentication
endpoints and shared security controls. The initial review was read-only. The
user subsequently authorized implementation of all findings. The local fixes
below supersede the original findings; the authorized production follow-up is
recorded next. This is
not a production security certification. Backend tests use disposable databases
outside the workspace; browser authentication probes are intercepted.

## Production release follow-up

### Cross-tab logout correction

The subsequent review identified cookie-only logout revoking a different tab's
session after another account signed in for the same company. Employee and HR
clients now send their memory-only bearer token on logout. The server verifies
its signature, surface, subject, session and tenant before revoking that family;
it clears a refresh cookie only when it belongs to the same family. A separate
login by the same account is also preserved. An expired access token can identify
its family for logout only, without refreshing into another account. Missing or
invalid bearer credentials fail without mutating cookies or sessions. Frontend
and backend must deploy together; an old open page must reload before sign-out.

The clean-environment tenant-mode finding was already corrected by `99e96f3`:
pytest and Playwright explicitly select header mode instead of relying on `.env`.
The new logout regressions failed on both old endpoints and pass with the fix.
Focused local checks: 84 backend authentication/tenancy tests and 12 desktop/phone
browser session regressions passed. The full browser suite passed 151 tests with
one intentional skip; frontend build, full Ruff and strict typing of all 368
backend source files passed. The full local backend suite passed 2,580 tests with
21 skips; PostgreSQL checks run in deployment CI. Deployment results are recorded
separately when complete.
The existing production acceptance limitations below still apply.

### Prior release evidence

The user authorized correcting every follow-up review finding, pushing for
deployment and monitoring on 3 October 2026. Release `99e96f30e0626301031bb040fba4d8945e4ee0bb`
deployed successfully in [run 37113839788](https://github.com/BenefitsCare25/BCP/actions/runs/37113839788).
This section supersedes the earlier local-only scope.

- CI passed 2,469 backend tests (115 skips), including all three real PostgreSQL
  migration/concurrent-refresh checks, and 147 desktop/phone browser tests (one
  intentional skip). Strict typing, lint, frontend build, dependency audits and
  the image's actual-command startup smoke check passed. Bicep validation passed
  in the preceding run; the corrected run also applied Bicep successfully.
- Private production migrations succeeded before the API/worker image switch.
  Both services independently returned HTTP 200 with the exact release SHA;
  readiness reports database and Redis OK. The workflow's stable-window smoke
  check passed, and independent probes remained healthy several minutes later.
- Fifteen deployed health/security/browser checks passed: employee and HR at
  1440/768/390/320px, required-field validation, generic invalid-credential
  feedback, no persisted bearer tokens, no page errors/horizontal overflow,
  zero automated axe violations, reduced-motion stills and 1440x1440 moving
  video. Both roles reject cross-origin refresh/logout. Browser credential
  responses were intercepted; no real password or account mutation was sent.
  Desktop and narrow-phone screenshots were visually reviewed. Evidence:
  gitignored `tmp/login-production-release/results.json` and screenshots.
- Outbound email remains disabled in the existing production setup. SMTP
  configuration and a real delivery acceptance test are still required for
  invitations. Real-account MFA/recovery, Safari and motion-accessibility
  acceptance remain gaps; automated axe results do not certify motion behavior.
- Production continues to use PostgreSQL and Azure storage. No local database,
  uploaded documents, fixture records or credentials were deployed. Existing
  company MFA policy choices were preserved. Existing shared-host sessions may
  need a fresh sign-in after this authentication upgrade.

- Docker's exec-form command now has valid escaped JSON quotes. CI starts the
  image with that actual command and requires its health endpoint to respond.
- Every invite credential replacement, including bulk delivery, revokes member
  sessions before the reset is committed or mailed. A failed delivery restores
  the prior password but does not resurrect sessions. Tests cover successful and
  failed resends and refresh attempts during mail delivery.
- Both API clients reject a refresh that changes account identity (and HR
  company), clear this tab's private query cache and never replay its pending
  request as the new account. They do not log out the other account's cookie.
- MFA recovery acknowledgement remains a memory-only requirement through token
  refresh. Recovery codes stay visible until acknowledged. HR profile queries
  key by identity instead of rotating tokens, preserving setup in progress.
- Only credential-verification calls suppress automatic refresh; employee MFA
  enrolment start refreshes a valid cookie after access-token expiry. Incorrect
  confirmation codes still remain inline.
- The local fixture script no longer copies the working database or reports a
  backup path. It and the local launcher are excluded from the production image.
- App Service logs through 2 October identify the platform peer as
  `169.254.129.1`. The production Bicep parameters explicitly trust only that
  peer and loopback. Proxy tests reject spoofed forwarded prefixes and ignore
  forwarded headers from an untrusted direct peer. No wildcard trust is used.
- Fresh local full-suite evidence: 2,568 backend tests passed, 18 skipped;
  147 Chromium desktop/phone tests passed, one intentional skip. The final
  proxy/startup/privacy tests passed (four), and the new three PostgreSQL
  migration/rotation tests are local skips because Docker is not running. They
  run mandatorily in GitHub deployment CI with an empty, loopback-only,
  disposable PostgreSQL 16 container. Full MyPy (367 modules), Ruff and the
  frontend build passed; Bicep compiled. Production rollout evidence follows
  once the workflow and independent health/readiness checks finish.

Unrelated pending runtime/packaging upgrades are excluded from this release.

The first release run (`37110949505`, commit `0a29392`) correctly blocked
deployment: six backend and six browser assertions exposed test servers that
defaulted to subdomain mode in CI but inherited header mode from local `.env`
files. Shared-host test configuration now explicitly selects header mode for
pytest and both Playwright servers, including disposable database setup.
Production defaults are unchanged; dedicated subdomain tests still override
the setting. Container startup, typing and dependency audits passed on that
run. Production remained on the prior healthy version during correction.
The corrected local focused run passed 64 backend tests (three PostgreSQL
checks skipped locally) and all 30 desktop/phone authentication browser tests.
All three PostgreSQL migration/concurrency checks passed in the first CI run.

No local database, credential file, review output, draft media or environment
secret belongs in the commit. Only the referenced login video/poster ship.
Existing optional MFA policies remain unchanged. Real-account enrolment,
invitation delivery and Safari acceptance must not be claimed from simulated
browser tests. Broader independent security certification is not implied.

## Implemented locally

| Original finding | Correction |
| --- | --- |
| Caller-controlled rate buckets | The trusted ASGI peer controls the bucket; tenant and forwarded headers cannot select it. Docker defaults to explicitly trusted proxy peers rather than `*`. |
| Unenforced required MFA | Separate optional/required policies for HR and employees, with broker settings. Required sessions allow only identity/status and MFA enrolment until confirmation. Required MFA cannot be disabled. Existing enrolled password-only sessions must authenticate again when the policy becomes required. |
| Persistent browser access tokens | Both roles use ten-minute, memory-only access tokens and rotating HttpOnly, SameSite=Strict, host-only refresh cookies; Secure outside development. Legacy persisted tokens are removed. |
| Incomplete logout | Access tokens bind to revocable session families; logout and password changes revoke server-side sessions. Idle and absolute limits apply to both roles. Revoked family roots also block descendants missed by a concurrent rotation. |
| Mixed company selection | HR entry queries are retained, role-specific company selection is tab-local, and memory supports blocked storage. The selected company is visible and changeable. Shared-host cookies are named separately for each role/company. |
| Missing frontend guards | Required/bounded credentials, accessible field errors, synchronous duplicate-submit guards, strict TOTP/recovery formats and expired-challenge restart paths. Password-setup forms have matching bounds/guards. Mistyped setup codes remain inline. |
| Misleading errors | Credential errors remain generic; outages, lockout, throttling and verified invite expiry are distinguishable. |

Session refresh deduplicates concurrent callers and uses Web Locks, when
available, to serialize rotating-cookie requests across tabs. A retried employee
request now sends the refreshed bearer token rather than its stale header.
New sign-ins and successful sign-outs discard role-specific cached account data.
Cookie refresh/logout reject cross-site and unapproved origins. A password change
returns the employee to sign-in because its old family has been revoked.

Migration `f4a6b8c0d2e4` adds mandatory-policy flags and session MFA/activity
metadata. It was applied only to the canonical `backend/inspro.db`. Integrity and
foreign-key checks passed; six company contexts and 9,151 employees remain.
No uploads, identities, coverage, elections or existing company policy settings
were reset. No backup or additional persistent database was created.

## Verification of the fixes

- Full backend suite: 2,560 passed, 18 skipped.
- Final backend rerun after the additional revoked-root race regressions:
  45 authentication/session tests passed. The final root guard was added after
  the full-suite run; both role-specific access and refresh refusal are covered.
- Full Chromium suite: 139 passed, one intentional duplicate-workbook skip.
- Final frontend rerun after session/shell cleanup: 36 passed, covering both
  roles' sign-in, password setup, MFA, refresh, restricted navigation, blocked
  storage and existing HR claim workflows on desktop and phone.
- TypeScript/Vite production build, Ruff across app/tests, MyPy on eight changed
  authentication modules and `git diff --check` passed.
- Ten clean-split layout/playback checks passed at 320-1920px; fresh desktop and
  phone screenshots were visually reviewed. Evidence:
  `tmp/portal-login-review/integrated/checks-layout.json` and adjacent captures.
- Local frontend returns HTTP 200; `/readiness` reports database OK. Redis is
  not required in this single-process development environment.
- New regression coverage: `backend/tests/test_portal_session_security.py` and
  `frontend/e2e/portal-login-security.spec.ts`. The abuse test explicitly enables
  SlowAPI and verifies throttling despite changing attacker-supplied headers.

## Deployment gate

These source changes affect production authentication when deliberately deployed;
local database consolidation does not select a production database. Deploy backend,
frontend and migration together using the existing production PostgreSQL URL.
The migration touches shared authentication/control tables, not employee rosters
or tenant operational data. Never deploy the local SQLite file or development
environment. Header-mode browser sessions require a one-time sign-in after the
cookie/token format change; older JWTs without a session id are rejected.

Set `FORWARDED_ALLOW_IPS` to the target ingress's actual trusted peer addresses or
networks. Do not use unrestricted `*` on a publicly reachable listener. An
unconfigured proxy makes all its users share a rate bucket; verify distinct
client addresses, spoof resistance and Redis-backed limits across workers before
release. The production template documents the variable; no live setting changed.

Verify HTTPS and Secure cookies, effective document security headers, exact CORS
origins, stable signing/encryption keys, Redis and SMTP in the deployment. Choose
required MFA per company; existing optional policies were intentionally preserved.
Run real-account invitation, password setup, enrolment/recovery, refresh/logout,
cross-company and Safari acceptance there. SQLite tests and the simulated
revoked-root regression do not establish PostgreSQL concurrency/load behavior.
Web Locks are not a cross-tab coordination guarantee in browsers lacking that API.

Self-service forgotten-password recovery remains a separate product decision;
company-admin/HR support is the current route. SSO remains explicitly excluded.
The approved visual/media baseline is unchanged; its previously documented motion
accessibility limitations remain.

Guidance consulted: [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

## Original findings (historical)

1. **High: anonymous rate-limit buckets are caller-controlled.**
   `backend/app/core/rate_limit.py:17` prefers arbitrary `X-Inspro-Client` and
   otherwise trusts the first `X-Forwarded-For` value. Public login routes do not
   authenticate the former header. A same-peer probe produced different buckets
   for two supplied client values. Account lockout still helps, but this bypasses
   the independent throttle for password spraying and expensive password hashing.
   Use a separate anonymous-auth key based on an IP resolved through trusted
   proxies; derive authenticated tenant keys from validated identity.

2. **High when MFA is intended to be mandatory: enabling MFA does not enforce
   enrolment.** `backend/app/api/v1/hr_auth.py:279` issues a normal session for an
   unenrolled HR user with an enrolment flag. The HR shell does not enforce it,
   and `backend/app/core/hr_auth.py:386` does not restrict authenticated API access
   pending enrolment. Employees also get a normal token before enrolment; their
   shell displays a notice (`PortalShell.tsx:373`). Distinguish optional from
   required MFA policy; for required MFA, issue an enrolment-only session and
   restrict it server-side until confirmation. Enrolled-user MFA is implemented.

3. **Medium: both access tokens persist in localStorage.**
   `frontend/src/stores/hrSession.ts:28` and `portalSession.ts:22` use Zustand's
   default persistence without excluding tokens. JavaScript executing in the
   origin can read both. HR's refresh cookie is already HttpOnly, SameSite=Strict,
   host-only and Secure outside development. Keep short access tokens in memory
   and restore sessions through protected refresh cookies, or adopt server-managed
   cookie sessions with appropriate CSRF protection. Employee sessions currently
   lack the HR refresh/session model.

4. **Medium: sign-out does not immediately invalidate all access tokens.**
   Employee sign-out (`PortalShell.tsx:150`) clears the browser store only; member
   JWTs are stateless (`backend/app/core/portal_auth.py:79`) with a default 12-hour
   lifetime. A copied token remains usable until expiry, account disablement or a
   password-version change. HR logout revokes refresh but its existing access
   token has no session/version check and remains usable for up to 10 minutes.
   Implement revocable sessions, defined idle/absolute expiry and password-reset
   invalidation appropriate to both roles. The default member TTL is not a
   measurement of the deployed production configuration.

5. **Medium: shared company selection changes an open HR tab's login tenant.**
   `frontend/src/lib/tenant.ts:37` uses one localStorage key across both roles and
   all tabs. Reproduced: open `/hr/sign-in?company=acme`, then
   `/portal/beta/sign-in`; the original HR tab sends `beta` on its next login.
   Server tenant checks prevent this alone from granting unauthorized access, but
   it can select the wrong company or cause unexplained login failures. Give HR a
   durable company URL like employee routes, visibly identify the selected company
   and make company changes explicit. Browser storage failure also lacks a typed
   company fallback: `rememberTenantSlug` returns success when persistence fails,
   while requests still read the missing stored value.

6. **Medium functional gap: frontend validation is incomplete in both roles.**
   Credential buttons reject empty values, identifiers are trimmed, company slugs
   are checked on submit, password visibility/autocomplete are present, and MFA
   accepts recovery codes. However, credential inputs lack required/maxLength
   constraints and the submit handlers do not guard validity or pending state.
   Browser probes submitted empty values through the handler and ordinary clicks
   sent 321-character identifiers and 257-character passwords. Server limits are
   320 and 256 and remain the authoritative protection. Align client limits with
   server contracts; show accessible field-level errors and guard submission.
   `canSubmitMfaCode` only checks length, so `abcdef` enables Verify. Validate the
   actual six-digit TOTP/recovery formats without excluding legitimate IDs or
   recovery codes. Expired MFA challenges remain on the same verification screen;
   provide a clear restart path and clear stale password/challenge state.

7. **Low: HR outage messages incorrectly blame credentials.**
   `frontend/src/routes/hr/sign-in.tsx:67` treats 503/network failures as incorrect
   credentials. Browser interception reproduced this. Match the employee form's
   distinction between invalid credentials, outages, lockout and throttling.
   Employee invite-expiry errors currently return 401 and are also hidden behind
   its generic credential error, despite the server already verifying the password
   before reporting expiry. Preserve actionable verified-account errors through
   explicit error codes.

## Controls already implemented

- Server-side bounded request schemas and SQLAlchemy credential lookups.
- Argon2id password hashes, dummy verification for nonexistent accounts and generic
  incorrect-password errors.
- Tenant-scoped login, role/type-separated JWT validation, active-account checks,
  password and MFA account lockout.
- Five-minute MFA challenges, replay-step tracking and hashed single-use recovery
  codes. Concurrency behavior was not evaluated in this review.
- Single-use password setup tokens through password-version checks; password
  length/strength policy and configurable breach checking on password setup.
- HR refresh rotation, reuse detection and idle/absolute lifetime controls.
- Member password changes invalidate previously issued member tokens.
- Security-header middleware, production signing-secret checks and required
  production Redis configuration exist in source.

## Original setup and acceptance notes

Verify HTTPS, effective frontend-document security headers, allowed CORS origins,
trusted proxy behavior, stable signing/encryption keys, Redis, SMTP delivery and
per-company MFA policy in the target deployment. This review did not query or
change production. Development HTTP is not evidence of production HTTPS behavior.

Use valid employee and HR accounts for end-to-end invitation, first-password,
MFA enrolment/recovery, login, refresh, logout and tenant-isolation acceptance.
Exercise real browser autofill, blocked storage and Safari. No real passwords were
submitted during this review.

Self-service forgotten-password recovery is not on these pages. Their current
support instruction is to contact HR/company administration. A secure recovery
flow is an optional product decision; SSO remains intentionally excluded by the
user's earlier requirement. Do not add unsupported decorative controls.

## Original review evidence

- `uv run pytest tests/test_hr_auth.py tests/test_portal_auth.py
  tests/test_tenancy_mode.py tests/test_middleware.py -q`: 58 passed.
- `uv run pytest tests/test_portal_login.py -q`: 9 passed.
- `node tmp/audit-login-validation.mjs`: both roles' invalid inputs, server-error
  handling, MFA format/expiry and cross-tab company behavior reproduced.
- Browser evidence: `tmp/portal-login-security-review/validation.json`.
- Isolated `_key_func` probe: one peer produced `ip:198.51.100.7`, then
  `client:attacker-bucket-a`, `client:attacker-bucket-b` and a supplied forwarded IP.
  This did not send a password attack to the running backend.
- The pytest fixture disables the SlowAPI layer, so passing authentication tests
  do not establish effective rate limiting. SQLite results do not establish
  PostgreSQL schema isolation or concurrency behavior.

OWASP guidance on browser session identifiers:
https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html
