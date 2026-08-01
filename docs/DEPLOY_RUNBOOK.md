# Deploy Runbook

End-to-end deploy procedure for Inspro. Audience: a person with `az` CLI
access to the Azure subscription and rights to create resource groups in
the Singapore region.

## One-time bootstrap (per environment)

```bash
# 1. Create the resource groups
az group create --name rg-inspro-shared   --location southeastasia
az group create --name rg-inspro-staging  --location southeastasia
az group create --name rg-inspro-prod     --location southeastasia

# 2. Create a shared Key Vault to hold cross-env secrets
az keyvault create \
  --name inspro-shared-kv \
  --resource-group rg-inspro-shared \
  --location southeastasia \
  --enable-rbac-authorization true

# 3. Seed the shared secrets
az keyvault secret set --vault-name inspro-shared-kv --name postgres-admin-password-staging --value "$(openssl rand -base64 32)"
az keyvault secret set --vault-name inspro-shared-kv --name postgres-admin-password-prod    --value "$(openssl rand -base64 32)"
# AI provider is per-tenant BYOK (entered on the frontend, stored encrypted) —
# no AI provider secret goes here. BYOK decryption needs INSPRO_AI_KEY_ENCRYPTION_KEY
# on App Service (Fernet key); generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Create the container registry
az acr create --name insproacr --resource-group rg-inspro-shared --sku Basic --admin-enabled true

# 5. Service principal for GitHub Actions (OIDC federated)
#    Configure federated credentials per https://learn.microsoft.com/azure/active-directory/develop/workload-identity-federation
```

## Pre-flight (per deploy)

Run through `docs/DEPLOY_PRE_FLIGHT.md` and tick every box.

## Apply Bicep

Substitute the placeholders in `infra/bicep/parameters.<env>.json` first
(`<SUBSCRIPTION_ID>`, `<TENANT_ID>`, `<CLIENT_ID_*>`). Then:

```bash
# Preview
az deployment group what-if \
  --resource-group rg-inspro-staging \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/parameters.staging.json

# Apply
az deployment group create \
  --resource-group rg-inspro-staging \
  --template-file infra/bicep/main.bicep \
  --parameters @infra/bicep/parameters.staging.json
```

This provisions: App Service, Postgres Flexible Server, Redis, Key Vault,
App Insights + Log Analytics. ~10 minutes for first run.

## Migrate database

After Bicep finishes, run migrations against the new Postgres:

```bash
export INSPRO_DATABASE_URL="postgresql+psycopg://insproadmin:<pw>@<host>:5432/inspro?sslmode=require"
cd backend
uv run alembic upgrade head
# Alembic migrations only touch the `public` schema. On Postgres each broker
# firm's operational tables live in a `firm_<id>` schema, so sync additive
# table/column changes into every firm schema. Idempotent; no-op on SQLite and
# before any firm schema exists. The CI deploy runs this automatically after
# every `alembic upgrade head` (see .github/workflows/deploy.yml).
uv run python -m scripts.provision_tenants
uv run python scripts/seed_demo.py   # optional: demo data only
```

Migrations run from a CI runner (or your laptop), which is *outside* the VNet —
see the note on the temporary firewall rule below.

## Seeding a new firm's reference library

A freshly created broker firm has an EMPTY attribute schema, product catalog and
insurer list, so every dropdown in the app is blank until it is seeded. These are
the Singapore defaults, not per-deployment data — do not copy them out of a dev
database (which also holds employee PII); seed them:

```bash
cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py
# or one firm only:
cd backend && PYTHONPATH=. uv run python scripts/seed_firm_library.py --firm <firm-id>
```

24 attributes + 25 products + 20 insurers. Idempotent, so re-run it after any
change to `SINGAPORE_ATTRIBUTES` / `PRODUCT_CATALOG` / `SG_INSURERS` to pick up
additions.

**Order matters — the firm must exist first.** These are TENANT tables: on
Postgres each firm has its own copy in `firm_<id>`, and `set_search_path` never
falls through to `public`. Seeding before any firm exists writes only to `public`,
where the app will never look, and the script exits with an error saying so.

1. Create the broker firm. **There is no UI for this** — Inspro owns the platform
   rather than being one tenant among many, so the "Broker firms" card was
   removed and bootstrap lives in the admin script:

   ```bash
   cd backend && PYTHONPATH=. uv run python -m scripts.create_system_admin \
       --email <you>@inspro.com.sg --name "<Your Name>" \
       --firm-name "Inspro Insurance Broker"
   ```

   It calls `provision_firm_schema`, which creates the schema before committing
   the row — do NOT insert the firm directly in SQL, an orphaned firm with no
   schema 500s every login for it.
2. Run `scripts/provision_tenants.py` so the firm schema gets its tables.
3. Run the seed above.
4. Create the client company (Access & Companies → Client companies).

Do NOT run `scripts/seed_demo.py` against production. It writes the same three
catalogs, but also creates a demo broker firm, two demo clients, a demo user and
a demo policy year.

Running it from a laptop needs a temporary firewall rule and the admin password
(both covered under "Database network access" below):

```bash
export INSPRO_DATABASE_URL="postgresql+psycopg://insproadmin:$(az keyvault secret show \
  --vault-name inspro-prod-kv --name postgres-admin-password --query value -o tsv)\
@inspro-prod-pg.postgres.database.azure.com:5432/inspro?sslmode=require"
```

## Database network access

The app reaches Postgres over a **private endpoint**, not the public internet.
`infra/bicep/modules/private-networking.bicep` provisions:

- `inspro-<env>-vnet` (`10.20.0.0/16`) with `snet-app` (delegated to
  `Microsoft.Web/serverFarms`, for regional VNet integration) and `snet-pe`
- a private endpoint on the Postgres server in `snet-pe`
- the `privatelink.postgres.database.azure.com` private DNS zone, linked to the
  VNet, with an A record written automatically by the endpoint's DNS zone group

Once the endpoint exists, Azure rewrites the server's public FQDN into a CNAME to
`<server>.privatelink.postgres.database.azure.com`. Inside the VNet the linked
zone resolves that to the private IP; outside it falls through to the public IP.
So the app needs no firewall rules at all.

`vnetRouteAllEnabled` is deliberately **off** — only VNet-bound traffic uses the
integration subnet, so Vertex AI, Entra, SMTP, ACR and Blob keep their existing
egress. Turning it on would require a NAT Gateway, because Azure has retired
default outbound access for new VNet deployments.

Verify the private path:

```bash
az network private-endpoint show -g rg-inspro-prod -n inspro-prod-pg-pe \
  --query "privateLinkServiceConnections[0].privateLinkServiceConnectionState.status"   # Approved
az network private-dns record-set a list -g rg-inspro-prod \
  -z privatelink.postgres.database.azure.com --query "[].{n:name,ip:aRecords[0].ipv4Address}"
az webapp show -g rg-inspro-prod -n inspro-portal --query virtualNetworkSubnetId
```

**Public access stays enabled with an empty allowlist** (which denies every
public client) purely so CI can migrate: deploy.yml opens a single-IP rule for
the runner and revokes it in an `always()` step. Closing public access entirely
means moving migrations inside the VNet — a container job on `snet-app`, or a
self-hosted runner — after which `publicNetworkAccess: 'Disabled'` can be set on
the server. That is the remaining hardening step.

To connect from your laptop (admin/psql), add a temporary rule for your own IP
and remove it afterwards:

```bash
IP=$(curl -fsS https://api.ipify.org)
az deployment group create -g rg-inspro-prod --name my-laptop-rule \
  --template-file infra/bicep/modules/ci-firewall-rule.bicep \
  --parameters postgresName=inspro-prod-pg ruleName=laptop-$USER allowedIp="$IP"
# ... then delete it
az rest --method delete --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/rg-inspro-prod/providers/Microsoft.DBforPostgreSQL/flexibleServers/inspro-prod-pg/firewallRules/laptop-$USER?api-version=2024-08-01"
```

Use the Bicep module rather than `az postgres flexible-server firewall-rule
create`: that command's `--name` flag means the SERVER on older Azure CLI and the
RULE on newer ones, which silently broke prod deploys once the runner image
updated.

### Break-glass: the app cannot reach the database

**There is no longer a public fallback.** Under the old allowlist a DNS or endpoint
fault still left 44 working public rules; now the private path is the *only* path, so
anything that breaks it takes the app down with it. Symptoms: `/health` 200 but
`/readiness` failing, or 5xx alerts with connection errors in App Insights.

Diagnose in this order — each command names the thing that must be true:

```bash
# 1. Endpoint still there and approved?
az network private-endpoint show -g rg-inspro-prod -n inspro-prod-pg-pe \
  --query "privateLinkServiceConnections[0].privateLinkServiceConnectionState.status"
# 2. A record still in the zone? (empty = the zone group was removed)
az network private-dns record-set a list -g rg-inspro-prod \
  -z privatelink.postgres.database.azure.com --query "[].aRecords[0].ipv4Address"
# 3. Zone still linked to the VNet? (unlinked = app resolves the PUBLIC ip, which
#    the empty allowlist then rejects — the most likely silent failure)
az network private-dns link vnet list -g rg-inspro-prod \
  -z privatelink.postgres.database.azure.com --query "[].virtualNetworkLinkState"
# 4. App still integrated?
az webapp show -g rg-inspro-prod -n inspro-portal --query virtualNetworkSubnetId
```

Re-running `az deployment group create` with `main.bicep` restores 1-4 — it is
idempotent and this is the normal fix, taking a few minutes.

If the private path cannot be restored quickly, restore service by re-opening the
public path — the app's outbound IPs still work, they were simply no longer allowlisted:

```bash
# Emergency only. Re-admits the app's ~44 shared outbound IPs; ~20 min to apply.
IPS=$(az webapp show --name inspro-portal --resource-group rg-inspro-prod \
  --query possibleOutboundIpAddresses -o tsv | tr ',' '\n' | sort -u)
for ip in $IPS; do
  az deployment group create -g rg-inspro-prod --name "emg-${ip//./-}" \
    --template-file infra/bicep/modules/ci-firewall-rule.bicep \
    --parameters postgresName=inspro-prod-pg ruleName="app-${ip//./-}" allowedIp="$ip"
done
```

Remove them again with `prune-app-firewall-rules.sh` once the private path is healthy.

### Migrating off the old outbound-IP allowlist

Superseded design: `sync-db-firewall.sh` declared one `app-<ip>` rule per App
Service outbound IP (~44). Azure applies each as its own server update, so a sync
took ~20 minutes per deploy. After the private endpoint is live and the app is
healthy, remove the leftovers once:

```bash
./infra/scripts/prune-app-firewall-rules.sh <subscription-id> rg-inspro-prod inspro-prod-pg
```

It refuses to run unless an Approved private endpoint exists, deletes
sequentially (concurrent server updates collide), and is safe to interrupt and
re-run.

## Deploy backend

Two paths — pick one:

**Container path (recommended for prod)**:

```bash
az acr login --name insproacr
docker build -t insproacr.azurecr.io/inspro-api:$(git rev-parse --short HEAD) backend
docker push insproacr.azurecr.io/inspro-api:$(git rev-parse --short HEAD)

az webapp config container set \
  --name inspro-staging-api \
  --resource-group rg-inspro-staging \
  --docker-custom-image-name insproacr.azurecr.io/inspro-api:<sha>
```

**Oryx build path (cheaper for staging)**:

```bash
cd backend
zip -r ../backend.zip . -x "*.pyc" -x ".venv/*" -x "__pycache__/*"
az webapp deploy \
  --resource-group rg-inspro-staging \
  --name inspro-staging-api \
  --src-path ../backend.zip \
  --type zip
```

## Deploy frontend

**There is nothing separate to deploy.** The SPA is built INTO the API image and
served by the API process, so a frontend change ships through the ordinary
container deploy above and needs no step of its own.

- `backend/Dockerfile` — a `frontend` stage runs `pnpm build` and copies `dist`
  into the runtime image as `./static`.
- `app/core/spa.py::mount_spa` — mounts it at `/`, called LAST in `main.py`
  because its catch-all would shadow any route registered after it. Hashed asset
  filenames are cached forever; `index.html` never is, or a deploy leaves browsers
  pinned to a stale bundle pointing at assets that no longer exist.

**Do not split the frontend onto its own host.** Same-origin is a requirement,
not a preference: the HR refresh cookie is host-only and `SameSite=Strict`
(`core/hr_auth.set_refresh_cookie`), so it is only ever returned to the exact
host that set it — a separately-hosted frontend calling a different API host
could never refresh a session. Tenancy is header-based on a single host
(`INSPRO_TENANT_MODE=header`), so there are no per-tenant subdomains to serve
either.

To confirm a frontend change actually reached prod, check the running image tag
against the commit and fetch an asset off the live host:

```bash
az webapp config show --name inspro-portal --resource-group rg-inspro-prod \
  --query linuxFxVersion -o tsv          # → …/inspro-api:<commit sha>
curl -s https://inspro-portal.azurewebsites.net/portal/coverage \
  | grep -oE '/assets/[^"]+\.css'        # then curl that asset and grep it
```

## Smoke test

```bash
APP=$(az webapp show --name inspro-staging-api --resource-group rg-inspro-staging --query defaultHostName -o tsv)
curl -i "https://${APP}/health"          # → 200 {"status":"ok"}
curl -i "https://${APP}/api/v1/policy-years"  # → 401 (auth enforced)
```

Open the SPA URL, sign in via Entra, upload a placement slip, run matching,
activate. Watch App Insights for any 5xx.

## Promote staging → prod

```bash
# Manual workflow trigger
gh workflow run deploy.yml --ref main

# Or, swap slots directly
az webapp deployment slot swap \
  --resource-group rg-inspro-prod \
  --name inspro-prod-api \
  --slot staging \
  --target-slot production
```

## Rollback

```bash
# Swap back if a prod swap broke things
az webapp deployment slot swap \
  --resource-group rg-inspro-prod \
  --name inspro-prod-api \
  --slot production \
  --target-slot staging

# Or: redeploy a known-good image tag
az webapp config container set \
  --name inspro-prod-api \
  --resource-group rg-inspro-prod \
  --docker-custom-image-name insproacr.azurecr.io/inspro-api:<previous-sha>
```

## Decommission

```bash
az group delete --name rg-inspro-staging --yes --no-wait
az group delete --name rg-inspro-prod --yes --no-wait
# Keep rg-inspro-shared (Key Vault retention is needed for any audit)
```
