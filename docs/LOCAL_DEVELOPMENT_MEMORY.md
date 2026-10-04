# Local development memory

User-approved setup, 3 October 2026. This policy supersedes older instructions to create persistent isolated demo or browser-review databases.

## Shared local data

- The sole persistent working database is `C:/Users/huien/inspro/backend/inspro.db`.
- Retained documents use `C:/Users/huien/inspro/backend/var/uploads/`.
- Broker, employee, HR and manual review sessions all use those stores. Separate roles and browser profiles do not imply separate databases.
- Do not create persistent `review.db`, `review-current.db`, demo databases or copied working databases. Do not reset, reseed or overwrite uploaded company data for a review.
- Use labelled additive review fixtures and respect real identities, coverage, elections and credentials.
- Automated tests that clear data must use disposable databases outside the workspace. These are temporary test resources, never a local environment for the user.
- The user explicitly requested no backups and removal of obsolete local database files. Do not create local backups unless subsequently requested.
- Keep secrets, SQLite files and retained documents out of version control.

## Startup

From the repository root:

```powershell
uv run --directory backend python -m scripts.local_dev
```

The launcher fixes the database and document paths, checks ports, applies normal migrations, and runs both services. It refuses to silently create an empty replacement when the shared database is missing. Ctrl+C stops both child servers. Logs are `backend/var/local-dev/api.log` and `web.log`.

Frontend: `http://localhost:5173/`. API: `http://127.0.0.1:8000`. Use `localhost` consistently in the browser: `127.0.0.1`, different ports and different browser profiles have different browser local storage.

Browser storage remembers preferences such as year selection, not employee/HR bearer tokens. Those short-lived tokens stay in memory; protected refresh cookies restore sessions. Company selection is role-specific and tab-local, with employee path/HR query context and a memory fallback when storage is blocked. Refresh cookies are separate for each role/company on the shared local host. Uploaded rosters, placement-slip data, benefit setups and retained documents live on the backend. If data appears missing, verify the database, selected company/year and frontend proxy before importing again or resetting anything.

The launcher preserves existing authentication configuration and the development encryption key. `backend/.env` pins the canonical SQLite URL and local storage, and retains a stable local portal JWT secret. The frontend proxy targets port 8000. Never replace the encryption key to start a review.

## Production isolation

The authorized login-security release subsequently deployed successfully as
`99e96f30e0626301031bb040fba4d8945e4ee0bb` in run `37113839788` on 3 October.
The workflow ran the private additive PostgreSQL migration before updating API
and worker images. Both serve the exact release SHA, and database/Redis
readiness is healthy. No local SQLite database, documents, review fixtures or
credentials were deployed. This does not change the sole working local store
or authorize local backups. The earlier consolidation record remains historical.

The user subsequently authorized a separate login-security production release
on 3 October 2026. This does not relax local-store isolation: the release must
use production PostgreSQL/Azure storage, never the workspace SQLite database or
fixture data. The fixture script's automatic full-database backup code was
removed after review, including its output metadata. Both local-only scripts
are excluded from Docker. Verification tests use disposable OS-temp SQLite
stores; the new PostgreSQL CI gate uses an empty ephemeral container without
host volumes. Release monitoring evidence is recorded in the login security
review; the consolidation statements below describe the earlier local work.

This policy applies to manual local development only. Production continues to use its configured PostgreSQL URL, tenant schemas, Azure document storage and Entra authentication. Never invoke the local launcher in a deployment, worker or migration job. The launcher rejects non-development environment settings and is excluded from the Docker build context. Production entrypoints and database-selection code are unchanged.

Local `.env` files, SQLite databases and `backend/var/` are excluded by both Docker packaging and the source handover filter. The frontend's port-8000 proxy is a Vite development-server setting; production serves the built frontend and API at the same origin. Consolidation and cleanup ran only against workspace SQLite files; no production database, configuration or deployment was changed.

## Consolidation evidence

On 4 October 2026, the shared database was brought to migration
`a5c7e9b1d3f6`. This additive change stores a broker account's authenticator
requirement, off by default as explicitly requested during development. Only
`system_admin` may change it under Users; changing it revokes broker sessions.
Existing enrolled authenticators are retained. Employee and HR company MFA
policies stay independent. Microsoft sign-in remains the broker's first factor,
including any separate MFA required by its Entra tenant policy.

The local upgrade preserved six users, one authenticator, 470 member accounts,
9,151 employees and six companies. SQLite integrity was OK, foreign-key checks
were clear, and all six user policies defaulted off. No database copy, backup,
reseeding or credential replacement was performed.

The source deployed as `c79298c3080fded2034219f5583012cbf4780911` in successful
[release run 37189523203](https://github.com/BenefitsCare25/BCP/actions/runs/37189523203).
The private PostgreSQL migration execution `inspro-prod-migrate-m4cgfub`
succeeded before the image rollout. CI passed 2,514 backend tests with 115 skips
and 203 browser checks with one skip; local focused runs passed 83 backend and
56 browser checks. Both services report the exact release, and database/Redis
readiness passed. Live desktop/mobile checks exercised the deployed policy gate
and admin control with intercepted synthetic identities, leaving production
accounts unchanged; real tenant credentials or authenticator codes were not
submitted. Continuous animation and media restrictions passed live checks.
Evidence is in `tmp/broker-policy-release-c79298c/` and
`tmp/broker-loop-restored-c79298c/`.

Post-deployment monitoring found zero HTTP 5xx across 190 portal and 19 worker
requests in ingested logs through 08:51:59 and 08:52:11 UTC respectively. Two
INFO-level socket-close messages at 08:49:11 explicitly belong to the previous
container shutting down. The subsequent console window, from 08:49:12 through
08:52:50 UTC, has zero error markers. Retained evidence records ingestion
timestamps; the log window is not claimed to cover every later request.

Subsequent login-security work on 3 October 2026 brought this same database to
`f4a6b8c0d2e4`. The additive migration stores mandatory-MFA policy and session
activity/proof metadata; it does not copy or replace application data. Read-only
checks returned integrity OK, zero foreign-key errors, six company contexts and
9,151 employees. Existing company MFA settings were preserved. No backup,
additional persistent database or production operation was performed. These
authentication source changes require their own intentional production release
and migration; see `PORTAL_LOGIN_SECURITY_REVIEW_2026-10-03.md` for deployment
requirements. The consolidation record below remains historical evidence.

The earlier startup used `backend/inspro.db`, but Go Ahead uploads were in review copies. Both missing Go Ahead company contexts were recovered into the shared database:

| Company | Employees | Placement-slip records | Benefit setups | Plans | Categories |
| --- | --- | --- | --- | --- | --- |
| GAS | 1,926 | 1 | 6 | 49 | 65 |
| GAS (local mapping review) | 1,926 | 5 | 7 | 27 | 27 |

The labelled mapping review remains a separate company context inside the same database because its revised parsing/mapping work must not overwrite the original upload. Existing CDL (491 employees), STM (4,806), STM demo (2) and VDL demo were retained. These counts describe the consolidation, not permanent expected totals.

The canonical database was brought to migration head `e3f5a7c9d1b2`; its historically missing `claim_notifications` table was restored, and the completed WICA schema was verified before reconciling its revision. SQLite integrity and foreign-key checks passed; recovered roster, policy-year, slip, setup, plan, category and mapping counts matched the source records before obsolete databases were removed.

Obsolete review/demo databases, previous local data copies, migration-smoke databases, stale test databases and their journal sidecars were removed without backups. The three old `tmp/start-*-review.py` launchers were removed. Browser/profile databases and compiler/security-tool caches are not application stores and are outside this cleanup.

Final verification on 3 October 2026:

- The shared launcher started frontend port 5173 and API port 8000; API readiness reported database OK.
- Frontend-proxied `/api/v1/me` lists both recovered GAS company contexts.
- Headless browser checks loaded both companies' benefits and 1,926-employee rosters, with zero API or JavaScript errors. Desktop and phone screenshots and results are in `tmp/single-local-db-check/`.
- Ruff passed for the launcher and temporary test setup; 17 focused private-migration, migration-helper, HR-origin and AI-policy migration tests passed.
- Backend tests now allocate and clean up their suite database in the operating-system temporary directory; frontend automated tests already do this.
- The complete 1,195-file source-package inventory excluded SQLite files, real local environments and retained storage. A configuration-only production check selected PostgreSQL and the local launcher rejected `INSPRO_ENV=prod`.
- Docker ignores local environments, database files, retained storage and the local-only launcher. Production runtime entrypoints, database-selection code, infrastructure settings and release workflows were not changed by this consolidation. No remote deployment or production database operation was performed.
