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
az keyvault secret set --vault-name inspro-shared-kv --name azure-foundry-api-key            --value "<paste-from-foundry-portal>"

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

Build and ship to the App Service (or split out to a Static Web App):

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm build
zip -r ../frontend.zip dist
az webapp deploy \
  --resource-group rg-inspro-staging \
  --name inspro-staging-web \
  --src-path ../frontend.zip \
  --type zip
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
