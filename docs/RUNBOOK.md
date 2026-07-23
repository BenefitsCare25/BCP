# Inspro Operations Runbook

Production operations reference. Audience: oncall engineer with backend access
and Azure Portal read/write on the Inspro subscription.

## Architecture at a glance

- **Backend** — FastAPI + SQLAlchemy on Azure App Service (Linux, Python 3.12).
- **Database** — Azure Database for PostgreSQL Flexible Server (Singapore region).
- **Cache** — Azure Cache for Redis (Singapore region). In-memory fallback if unreachable.
- **AI provider** — AWS Bedrock (Anthropic Claude Sonnet, Singapore/APAC-resident), configured per-tenant via the frontend BYOK page.
- **Secrets** — Azure Key Vault, accessed via App Service managed identity.
- **Observability** — Application Insights + Log Analytics workspace.
- **Frontend** — React/Vite/Tailwind built into `dist/`, served by the App Service alongside the API.

## Deploys

See `docs/DEPLOY_RUNBOOK.md` for the canonical deploy procedure. Summary:

1. CI builds artefacts on merge to `main` (`.github/workflows/deploy.yml`).
2. Staging slot auto-receives the build.
3. Manual approval gate swaps staging → prod.

Rollback: in Azure Portal → App Service → Deployment slots → **Swap** to flip
prod ↔ previous slot. Or `az webapp deployment slot swap` from CLI.

## Routine operations

### "Force re-match a policy year"

A category was edited after the matcher ran; some employees show stale matches.

```bash
curl -X POST "$INSPRO_URL/api/v1/match-results/run?policy_year_id=$PY" \
  -H "Authorization: Bearer $TOKEN"
```

(Or click "Re-run matching" in the Operations workspace.) Rate-limited to
10/min per client.

### "Clear failed AI cache entries"

If the AI prompt template changed and stale entries return wrong rules:

```bash
redis-cli -u "$INSPRO_REDIS_URL" KEYS "ai:rule_generation/v1:*" | xargs redis-cli DEL
```

Better: bump `PROMPT_VERSION` in `app/services/ai_gateway.py` so old cache
entries are abandoned automatically.

### "Reset a client's monthly AI budget mid-cycle"

Direct DB update (no admin UI yet):

```sql
UPDATE clients SET ai_monthly_token_budget = <new_value> WHERE id = '<client_id>';
```

Audit this with a `policy_change` row in `audit_log` so the change is traceable.

### "Activate a policy year manually"

Use the UI button. If it's disabled, check `/api/v1/policy-years/<id>/activation-readiness`
— probably some categories are still `needs_review`.

### "Roll back an activation"

Activation is one-way by design. To "fix" an active year, create a new draft
policy year (clone from the snapshot) and have downstream consumers cut over.
The clone-from-snapshot endpoint is a follow-up — for now, manual:

```sql
-- approximate; verify with engineering before running
INSERT INTO policy_years (id, client_id, year, status) ...
```

## Triage

### "API returning 5xx"

1. **Application Insights** → Failures → group by exception type.
2. Common: `CircuitOpenError` means the AI provider is failing; breaker will
   self-recover in ~60s.
3. `psycopg.OperationalError` → check Postgres metrics; restart App Service
   if connections are exhausted.

### "AI budget exceeded warnings spiking"

Schema workspace AI usage tile shows budget %. If a single client is at >100%:

1. Confirm with the client they're aware of the AI spend.
2. Temporarily raise budget (see "Reset a client's monthly AI budget").
3. Investigate why — usually a misconfigured category triggering AI fallback
   in a loop.

### "Audit log growing too fast"

Audit rows are append-only by design. Volume thresholds aren't hardcoded;
when storage cost becomes a concern, archive rows older than 7 years to
Azure Blob (PDPA retention is the floor).

## Where things live

- **Audit logs**: Postgres `audit_log` table. Filter by `(action, entity_type, entity_id)`.
- **AI spend rows**: Postgres `ai_spend_log` table.
- **App logs**: App Service → Log stream, or Log Analytics workspace.
- **Snapshots**: Postgres `policy_years.snapshot_json` JSONB column.
- **Secrets**: Key Vault (`inspro-prod-kv` / `inspro-staging-kv`).

## Escalation

- **L1**: oncall engineer (this runbook).
- **L2**: backend lead. Contact placeholder — update on assignment.
- **Security incident**: trigger PDPA breach response per `docs/INCIDENT_RESPONSE.md` (not yet written; create on first incident).
