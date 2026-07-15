# Security Review — Phase 10

Walkthrough against the OWASP API Security Top 10 (2023). Status legend:
**PASS** = no finding · **FIX** = fixed inline as part of Phase 10 ·
**TODO** = follow-up tracked (severity in parens).

Reviewed against commit shipped at end of all-phases session, 2026-05-12.

| # | OWASP API risk | Status | Notes |
|---|---|---|---|
| API1 | Broken Object Level Authorization (BOLA) | **PASS** | Every mutating endpoint takes `user: CurrentUser = Depends(get_current_user)` and queries scope by `user.client_id`. Snapshot endpoint `GET /policy-years/{id}/snapshot` reads through the model — confirm tenant scoping when Entra lands (Phase 9.5). |
| API2 | Broken Authentication | **TODO (high)** | Mock auth is in place. Entra swap is Phase 9.5 of the same session — once landed, all routes are JWT-protected and the cookie session is `httpOnly`/`samesite=lax`. Until then, deploy with `INSPRO_AUTH_MODE=mock` is dev-only. |
| API3 | Broken Object Property Level Auth | **PASS** | Pydantic response models (`CategoryOut`, `EmployeeOut`, etc.) act as allowlists — internal fields like `audit_log.before`/`after` raw payloads never leak through to clients. |
| API4 | Unrestricted Resource Consumption | **FIX** | SlowAPI rate-limit (`120/minute` default, `10/minute` on `/match-results/run`). Per-client keys, env-tunable (`INSPRO_RATE_LIMIT_DEFAULT`, `INSPRO_RATE_LIMIT_ENABLED`). AI cost capped per-client via `ai_monthly_token_budget`. |
| API5 | Broken Function Level Authorization | **TODO (medium)** | Role enum is `broker_admin`/`broker_viewer`/`client_admin`/`client_hr`/`system_admin` but only `system_admin` is exercised. Add role-gating on destructive endpoints (bulk-delete categories/employees) — track for Phase 9.5 alongside Entra. |
| API6 | Unrestricted Access to Sensitive Business Flows | **FIX** | `/match-results/run` is rate-limited; activation is one-way (409 on second call); placement-slip upload uses `saved_upload` context manager with extension allowlist (`.xls/.xlsx/.xlsm`). |
| API7 | Server-Side Request Forgery | **PASS** | No code path takes a user-supplied URL and fetches it. AI provider URL comes from env, not request. |
| API8 | Security Misconfiguration | **FIX** | CORS allowlist no longer hardcoded — driven by `INSPRO_CORS_ORIGINS` env so prod can't accidentally accept `*`. Audit-log row redaction (`_REDACT_KEYS_EXACT`) drops secret-bearing keys before persistence. |
| API9 | Improper Inventory Management | **TODO (low)** | API versioned at `/api/v1/*`. No `/v2` exists yet — once the v2 export endpoints land (deferred), document deprecation timelines in `docs/API.md`. |
| API10 | Unsafe Consumption of APIs | **FIX** | AI provider responses validated via the `RuleEnvelope` Pydantic shape before write. Provider errors return generic 502 + `logger.exception` server-side (no upstream stack leak). Circuit breaker prevents cascading failure. |

## Additional checks performed

- **Secrets in code/logs**: grepped for `f".*{settings.\w+}"` patterns. None leak credentials. `_REDACT_KEYS_EXACT` in `app/core/audit.py` covers the audit-row vector.
- **SQL injection**: SQLAlchemy 2.x ORM throughout; no `text()` with user input.
- **File upload safety**: `saved_upload` uses temp files + extension allowlist; size limits applied at FastAPI middleware default (1MB body unless overridden) — increase via uvicorn `--limit-request-body` in prod.
- **Dependency vulnerability surface**: minimal — Pydantic, FastAPI, SQLAlchemy, psycopg, openpyxl, xlrd, redis, slowapi, pyjwt, cachetools, anthropic. Run `uv pip audit` periodically; CI step is a follow-up.

## Open follow-ups (post-this-session)

1. **API5 role-gating**: add `Depends(require_role("broker_admin"))` decorator on bulk-delete + activate routes. Estimated 30 min once Entra is in place.
2. **CSP headers**: serve frontend with `Content-Security-Policy: default-src 'self'; ...` once Entra ID is wired so OAuth flow is in scope.
3. **Dependency-vulnerability CI gate**: add a step in `.github/workflows/deploy.yml` that runs `uv pip audit` and fails on `high`/`critical`.
4. **App Insights alerting**: configure Azure alerts on 5xx rate, AI breaker tripping, audit-log volume anomalies. Owner: ops on first deploy.
