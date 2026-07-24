# Deploy Pre-Flight Checklist

Run this before any deploy that touches prod (staging is freely re-deployable).

## Backend

- [ ] `cd backend && uv run pytest` — all tests green
- [ ] `uv run ruff check app tests scripts` — clean
- [ ] `uv run alembic upgrade head` — succeeds against a fresh DB (staging)
- [ ] No `INSPRO_AUTH_MODE=mock` in prod env vars
- [ ] `INSPRO_AI_KEY_ENCRYPTION_KEY` set on App Service (decrypts per-tenant BYOK AI keys) — without it all BYOK-configured AI silently fails closed
- [ ] Each active company has a Google Vertex AI (Gemini) BYOK key configured on `/configuration/ai-provider` (service-account JSON, `asia-southeast1`) — otherwise that company's AI review degrades to manual
- [ ] `INSPRO_DATABASE_URL` references the prod Postgres (verify region = Singapore)
- [ ] `INSPRO_REDIS_URL` references the prod Redis (or unset to force in-memory fallback — acceptable for single-instance)
- [ ] `INSPRO_CORS_ORIGINS` lists only the prod frontend origin(s)
- [ ] Application Insights connection string set

## Frontend

- [ ] `cd frontend && pnpm build` — clean under TypeScript strict
- [ ] `VITE_API_BASE_URL` points at the prod API origin
- [ ] Entra `client_id` / `tenant_id` env vars match the prod app registration

## Data / migrations

- [ ] Migration applied to staging first; staging smoke-tested
- [ ] DB backup taken before prod migration (Azure Database for PostgreSQL: automated)
- [ ] If migration adds a NOT NULL column without default → confirm backfill strategy

## Security

- [ ] OWASP review (`docs/SECURITY_REVIEW.md`) updated for any new endpoints
- [ ] No secrets committed (grep `.env`, `.pem`, `.key` in the diff)
- [ ] `pyproject.toml` deps pinned to known versions

## Communication

- [ ] If user-visible behaviour changes — notify client admins ≥24h ahead
- [ ] If breaking schema change — coordinate with downstream insurer export consumers (v2 scope, mostly not applicable today)
