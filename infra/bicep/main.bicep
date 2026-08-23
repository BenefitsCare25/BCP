// Inspro — top-level deployment template.
//
// Usage (CI runs this with parameter overrides for secrets — see deploy.yml):
//   az deployment group create \
//     --resource-group rg-inspro-staging \
//     --template-file main.bicep \
//     --parameters @parameters.staging.json \
//     --parameters postgresAdminPassword=<from-kv> \
//                  entraTenantId=<github-secret> \
//                  entraClientId=<github-secret> \
//                  containerImage=<acr>.azurecr.io/inspro-api:<sha>
//
// Resources provisioned (Singapore region):
// - App Service Plan + Linux API and claim-review worker apps (containers)
// - Azure Database for PostgreSQL Flexible Server
// - Azure Cache for Redis
// - Key Vault (secrets read by App Service managed identity at runtime)
// - Application Insights + Log Analytics workspace
// - Diagnostic settings shipping logs/metrics to the LAW
// - HTTP 5xx alert against App Insights

targetScope = 'resourceGroup'

@description('Environment short name (staging | prod).')
@allowed(['staging', 'prod'])
param env string

@description('Azure region. Singapore for PDPA.')
param location string = 'southeastasia'

@description('App Service Plan SKU.')
param appServicePlanSku string

@description('Postgres SKU.')
param postgresSku string

@description('Postgres tier.')
param postgresTier string

@description('Postgres admin username.')
param postgresAdminUser string

@secure()
@description('Postgres admin password (typically from KV secret, passed via CI --parameters override).')
param postgresAdminPassword string

@description('Postgres backup retention days. Prod overrides to 35.')
param postgresBackupRetentionDays int = 14

@description('Enable geo-redundant Postgres backups. NOTE: the paired region for southeastasia is East Asia (Hong Kong), so enabling this copies member/claims data OUT of Singapore. Leave false unless that has been signed off against PDPA.')
param postgresGeoRedundantBackup bool = false

@description('Postgres data disk size. IOPS is derived from it, NOT from the compute SKU: 32GiB=120, 64GiB=240, 128GiB=500, 256GiB=1100. 32GiB is the throughput floor.')
param postgresStorageGB int = 32

@description('Enable zone-redundant Postgres HA. Not supported on the Burstable tier, and roughly doubles cost (a standby duplicates compute+storage).')
param postgresHighAvailability bool = false

@description('Azure Managed Redis SKU, for example Balanced_B0.')
param redisSku string

@description('Provision Redis at all. Both consumers degrade gracefully without it — the AI response cache falls back to in-memory and SlowAPI to per-process counters (see app/services/ai_cache.py and app/core/rate_limit.py) — so non-prod can skip the cost. Prod keeps it: per-process rate-limit counters would multiply every limit by the worker count.')
param deployRedis bool = true

@description('Entra tenant ID.')
param entraTenantId string

@description('Entra client ID for the API app registration.')
param entraClientId string

@description('CORS origins (comma-separated).')
param corsOrigins string

// Claims AI provider is configured per-tenant via the frontend BYOK page
// (client_ai_configs, encrypted), NOT via deployment env vars — so no AI
// provider params are declared here.

@description('Fully-qualified container image, e.g. insproacr.azurecr.io/inspro-api:<sha>. The CI must build+push the image before passing this in.')
param containerImage string

@description('Signing secret for employee-portal member JWTs (min 32 chars). The app refuses to boot in prod without one — generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"')
@secure()
param portalJwtSecret string

@description('Fernet master key that decrypts per-tenant BYOK AI keys (client_ai_configs). Vertex/Gemini BYOK is the sole AI path in prod, so without this every decrypt fails and AI silently falls closed. Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
@secure()
param aiKeyEncryptionKey string

@description('ACR registry hostname for the webapp to pull from, e.g. insproacr.azurecr.io.')
param acrLoginServer string

@description('Resource group holding the shared container registry.')
param acrResourceGroup string = 'rg-inspro-shared'

@description('Web app / hostname. Defaults to inspro-<env>-api; prod uses inspro-portal so the public URL is inspro-portal.azurewebsites.net.')
param siteName string = ''

@description('Apex domain for tenant-per-subdomain routing. Empty on single-host deployments (no custom domain).')
param baseDomain string = ''

@description('How HR/portal requests name their tenant. "subdomain" needs wildcard DNS + a wildcard cert; "header" is for a single shared hostname.')
@allowed(['subdomain', 'header'])
param tenantMode string = 'header'

@description('Gunicorn worker processes. Keep at or below the plan vCPU count — it also divides the AI concurrency limit and multiplies the DB pool.')
param webConcurrency int = 2

@description('SQLAlchemy pool size PER WORKER. Ceiling is workers x (pool + overflow) x instances; keep it under the server max_connections.')
param dbPoolSize int = 3

@description('SQLAlchemy overflow connections per worker.')
param dbMaxOverflow int = 2

@minValue(1)
@maxValue(16)
@description('Concurrent durable claim reviews in the dedicated worker process.')
param reviewWorkerConcurrency int = 1

@minValue(1)
@maxValue(16)
@description('Hard concurrent claim-review cap per company. Must not exceed reviewWorkerConcurrency.')
param reviewWorkerMaxConcurrentPerClient int = 1

@minValue(1)
@maxValue(64)
@description('SQLAlchemy pool size for the dedicated claim-review worker process.')
param reviewWorkerDbPoolSize int = 4

@minValue(0)
@maxValue(64)
@description('SQLAlchemy overflow connections for the dedicated claim-review worker process.')
param reviewWorkerDbMaxOverflow int = 2

@description('Integrate the web app with the VNet so it reaches Postgres over the private endpoint. Setting this to false stops NEW deployments wiring the subnet, but does not tear down existing integration — for a rollback run `az webapp vnet-integration remove` as well, which reverts the app to the public path.')
param enableVnetIntegration bool = true

@description('Notification email for HTTP 5xx alerts (optional).')
param alertEmail string = ''

@description('Portal mail delivery mode. Use disabled until a verified STARTTLS SMTP sender is configured. Log is retained only for backward-compatible rollout and is normalized to disabled by production builds.')
@allowed(['disabled', 'log', 'smtp'])
param mailMode string = 'log'

@description('SMTP host for portal OTP and claim-update mail.')
param smtpHost string = ''

@description('SMTP port.')
param smtpPort string = '587'

@description('SMTP username (also the default From address).')
param smtpUser string = ''

@description('From address for portal OTP mail (defaults to smtpUser).')
param smtpFrom string = ''

@secure()
@description('SMTP password (passed via CI --parameters override).')
param smtpPassword string = ''

var prefix = 'inspro-${env}'
var isProd = env == 'prod'
var appName = empty(siteName) ? '${prefix}-api' : siteName
var acrName = split(acrLoginServer, '.')[0]

// Built-in role IDs (https://learn.microsoft.com/azure/role-based-access-control/built-in-roles).
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// ── Observability ───────────────────────────────────────────────────────────
resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-law'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-appi'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

// ── Key Vault — created BEFORE Postgres/Redis so we can write secrets in. ──
resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: '${prefix}-kv'
  location: location
  properties: {
    tenantId: entraTenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    // Prod KVs hold DB admin passwords — keep them recoverable for 90 days
    // matching Azure's recommended default.
    softDeleteRetentionInDays: isProd ? 90 : 30
    // Must be `true` or ABSENT — Azure rejects an explicit `false` ("cannot be
    // set to false ... irreversible action"), so `isProd` alone failed every
    // non-prod deployment. Purge protection is deliberately prod-only: it makes
    // the vault unrecoverable-by-deletion for the retention window, which is
    // right for prod secrets but would strand throwaway staging vaults.
    enablePurgeProtection: isProd ? true : null
  }
}

// ── Postgres ─────────────────────────────────────────────────────────────────
resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${prefix}-pg'
  location: location
  sku: {
    name: postgresSku
    tier: postgresTier
  }
  properties: {
    version: '16'
    administratorLogin: postgresAdminUser
    administratorLoginPassword: postgresAdminPassword
    storage: {
      storageSizeGB: postgresStorageGB
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: postgresBackupRetentionDays
      geoRedundantBackup: postgresGeoRedundantBackup ? 'Enabled' : 'Disabled'
    }
    // Explicit param, not `isProd`: ZoneRedundant is unsupported on Burstable,
    // so hardcoding it by environment made a prod deploy on B-series fail
    // outright. Scaling up later re-enables it with a parameter flip.
    highAvailability: {
      mode: postgresHighAvailability ? 'ZoneRedundant' : 'Disabled'
    }
  }
}

resource postgresDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: 'inspro'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.UTF8'
  }
}

// ── Private network path to Postgres ────────────────────────────────────────
// The app reaches the database over a private endpoint in our own VNet, NOT
// over the public internet through a firewall allowlist.
//
// This replaced `infra/scripts/sync-db-firewall.sh`, which enumerated the App
// Service's ~44 `possibleOutboundIpAddresses` as individual firewall rules.
// Azure applies each rule as its own server update, so a full sync took ~20
// minutes of repeated live-database reconfiguration; the IPs are re-issued on
// any plan-tier change (silently locking the app out until a re-sync); and they
// are shared App Service infrastructure addresses, so allowlisting them also
// admitted other tenants on the same scale unit.
//
// Public network access stays ENABLED on the server but with an EMPTY allowlist,
// which denies every public client. It is not disabled outright only because CI
// runs Alembic from a GitHub runner, which opens a single-IP rule for itself and
// revokes it in an always() step (see deploy.yml). Closing public access fully
// requires moving migrations inside the VNet — see docs/DEPLOY_RUNBOOK.md.
module privateNetworking 'modules/private-networking.bicep' = {
  name: 'private-networking-${env}'
  params: {
    prefix: prefix
    location: location
    postgresName: postgres.name
    redisName: '${prefix}-redis'
    deployRedis: deployRedis
    storageName: replace('${prefix}docs', '-', '')
  }
  dependsOn: [
    redis
    storage
  ]
}

// ── Redis ────────────────────────────────────────────────────────────────────
resource redis 'Microsoft.Cache/redisEnterprise@2025-07-01' = if (deployRedis) {
  name: '${prefix}-redis'
  location: location
  sku: {
    name: redisSku
  }
  identity: { type: 'None' }
  properties: {
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Disabled'
    highAvailability: isProd ? 'Enabled' : 'Disabled'
    encryption: {}
  }
}

resource redisDatabase 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = if (deployRedis) {
  parent: redis
  name: 'default'
  properties: {
    // The app reads the TLS access key from Key Vault. Public network access
    // remains disabled, and this opt-in is required before listKeys() is valid.
    accessKeysAuthentication: 'Enabled'
    clientProtocol: 'Encrypted'
    clusteringPolicy: 'EnterpriseCluster'
    evictionPolicy: 'AllKeysLRU'
    port: 10000
  }
}

// ── Seed secrets into KV ────────────────────────────────────────────────────
// App settings reference these via @Microsoft.KeyVault(...). Secrets never
// appear in the App Service "Configuration" blade in plaintext.
resource kvSecretPgPassword 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'postgres-admin-password'
  properties: {
    value: postgresAdminPassword
  }
}

resource kvSecretDatabaseUrl 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'database-url'
  properties: {
    value: 'postgresql+psycopg://${postgresAdminUser}:${postgresAdminPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/inspro?sslmode=require'
  }
}

resource kvSecretRedisUrl 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = if (deployRedis) {
  parent: kv
  name: 'redis-url'
  properties: {
    value: 'rediss://:${uriComponent(redisDatabase!.listKeys().primaryKey)}@${redis!.name}.${location}.redis.azure.net:10000/0'
  }
}

resource kvSecretPortalJwt 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'portal-jwt-secret'
  properties: {
    value: portalJwtSecret
  }
}

resource kvSecretAiKeyEncryption 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'ai-key-encryption-key'
  properties: {
    value: aiKeyEncryptionKey
  }
}

resource kvSecretSmtpPassword 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = if (mailMode == 'smtp') {
  parent: kv
  name: 'smtp-password'
  properties: {
    value: smtpPassword
  }
}

// ── Retained document storage (claim receipts, dependant proofs — PII) ──────
// Private blob container; the app reads/writes via managed identity
// (INSPRO_STORAGE_MODE=azure + INSPRO_STORAGE_ACCOUNT_URL, no keys in config).
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace('${prefix}docs', '-', '')
  location: location
  // ZRS, not GRS. This container holds claim receipts, referral letters and
  // dependant proofs — PII. Geo-redundant storage replicates to the paired
  // region, which for southeastasia is East Asia (Hong Kong), taking the data
  // out of Singapore. ZRS keeps three copies across Singapore availability
  // zones instead, and costs less.
  sku: { name: isProd ? 'Standard_ZRS' : 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
    allowSharedKeyAccess: false // managed identity only — no key leakage path
    accessTier: 'Hot'
  }
}

resource storageBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    // PII documents — recoverable for 30 days after an accidental delete.
    deleteRetentionPolicy: { enabled: true, days: 30 }
    containerDeleteRetentionPolicy: { enabled: true, days: 30 }
  }
}

resource documentsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: storageBlobService
  name: 'documents'
  properties: { publicAccess: 'None' }
}

// ── App Service ──────────────────────────────────────────────────────────────
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: '${prefix}-plan'
  location: location
  sku: { name: appServicePlanSku }
  kind: 'linux'
  properties: { reserved: true }
}

// App settings: explicit secrets reference Key Vault by URI so the
// managed-identity Secret User role grant below makes them readable at
// runtime. Plain values stay inline.
var commonAppSettings = [
  { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://${acrLoginServer}' }
  { name: 'INSPRO_ENV', value: env }
  { name: 'INSPRO_AUTH_MODE', value: 'entra' }
  { name: 'INSPRO_ENTRA_TENANT_ID', value: entraTenantId }
  { name: 'INSPRO_ENTRA_CLIENT_ID', value: entraClientId }
  // The BARE client id, not `api://<id>`. The app registration is set to
  // requestedAccessTokenVersion=2, and a v2 access token carries `aud` = the
  // client id GUID, whereas a v1 token carries the App ID URI. The two settings
  // are coupled: v1 pairs `api://<id>` with issuer sts.windows.net/<tid>/, v2
  // pairs the bare id with login.microsoftonline.com/<tid>/v2.0. Mixing them
  // fails audience-or-issuer validation, which surfaces as an infinite
  // sign-in redirect loop (401 -> client.ts calls signIn() -> repeat).
  { name: 'INSPRO_ENTRA_AUDIENCE', value: entraClientId }
  { name: 'INSPRO_ENTRA_ISSUER', value: '${environment().authentication.loginEndpoint}${entraTenantId}/v2.0' }
  { name: 'INSPRO_ENTRA_JWKS_URL', value: '${environment().authentication.loginEndpoint}${entraTenantId}/discovery/v2.0/keys' }
  { name: 'INSPRO_CORS_ORIGINS', value: corsOrigins }
  // Tenant routing. On a single host the Host header can't name a tenant, so
  // the SPA sends X-Inspro-Tenant-Slug instead — see app/core/tenancy_host.py.
  { name: 'INSPRO_TENANT_MODE', value: tenantMode }
  { name: 'INSPRO_BASE_DOMAIN', value: baseDomain }
  { name: 'INSPRO_DATABASE_URL', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretDatabaseUrl.name})' }
  { name: 'INSPRO_PORTAL_JWT_SECRET', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretPortalJwt.name})' }
  { name: 'INSPRO_AI_KEY_ENCRYPTION_KEY', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretAiKeyEncryption.name})' }
  // Production maps legacy `log` to disabled during the backward-compatible
  // rollout. SMTP passwords stay in Key Vault when SMTP is enabled later.
  { name: 'INSPRO_MAIL_MODE', value: mailMode }
  { name: 'INSPRO_SMTP_HOST', value: smtpHost }
  { name: 'INSPRO_SMTP_PORT', value: smtpPort }
  { name: 'INSPRO_SMTP_USER', value: smtpUser }
  { name: 'INSPRO_SMTP_FROM', value: smtpFrom }
  { name: 'INSPRO_SMTP_PASSWORD', value: mailMode == 'smtp' ? '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=smtp-password)' : '' }
  { name: 'INSPRO_STORAGE_MODE', value: 'azure' }
  { name: 'INSPRO_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'INSPRO_STORAGE_CONTAINER', value: documentsContainer.name }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
]

// Appended only when Redis exists. An unset INSPRO_REDIS_URL is the documented
// signal for both consumers to fall back to in-memory, whereas a KeyVault
// reference to a missing secret would surface as an opaque startup failure.
var redisAppSettings = deployRedis ? [
  { name: 'INSPRO_REDIS_URL', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=redis-url)' }
] : []
var webAppSettings = concat(commonAppSettings, redisAppSettings, [
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'WEB_CONCURRENCY', value: string(webConcurrency) }
  // The API pool is per Gunicorn process; change it with WEB_CONCURRENCY.
  { name: 'INSPRO_DB_POOL_SIZE', value: string(dbPoolSize) }
  { name: 'INSPRO_DB_MAX_OVERFLOW', value: string(dbMaxOverflow) }
  { name: 'OTEL_SERVICE_NAME', value: '${prefix}-api' }
])

// Container-based deploy with ACR pull via managed identity.
var siteConfig = {
  linuxFxVersion: 'DOCKER|${containerImage}'
  acrUseManagedIdentityCreds: true
  ftpsState: 'Disabled'
  minTlsVersion: '1.2'
  http20Enabled: true
  healthCheckPath: '/readiness'
  appSettings: webAppSettings
}

resource webapp 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    // Regional VNet integration — how the app reaches Postgres privately.
    // Supported on Basic since the tier restriction was lifted; the old comment
    // claiming otherwise was stale.
    //
    // `vnetRouteAllEnabled` is deliberately LEFT OFF. Only traffic bound for the
    // VNet's own address space is routed through the integration subnet, which
    // is all we need (the private endpoint lives there). Turning route-all on
    // would push public calls — Vertex AI, Entra JWKS, SMTP, ACR, Key Vault —
    // through the VNet, which then needs a NAT Gateway. Blob and Redis resolve
    // to their private endpoints and are already routed privately.
    // Private DNS still resolves without it: an integrated app inherits the
    // VNet's DNS configuration, and the zone is linked to that VNet.
    virtualNetworkSubnetId: enableVnetIntegration ? privateNetworking.outputs.appSubnetId : null
    siteConfig: union(siteConfig, { alwaysOn: isProd })
  }
  // The Redis secret name is a literal in `redisAppSettings`, so Bicep infers
  // no dependency on it. Without this the app is created while Redis is still
  // provisioning (~15 min), the KeyVault reference resolves to SecretNotFound,
  // and App Service CACHES that failure — the container then boots with the
  // literal "@Microsoft.KeyVault(...)" string as its Redis URL and dies in
  // SlowAPI with "unknown storage scheme".
  dependsOn: deployRedis ? [kvSecretRedisUrl] : []
}

// Dedicated durable claim-review executor. It shares the image and private
// dependencies but has an independent process lifetime from Gunicorn.
var workerAppSettings = concat(commonAppSettings, redisAppSettings, [
  { name: 'WEBSITES_PORT', value: '8081' }
  { name: 'PORT', value: '8081' }
  { name: 'WEB_CONCURRENCY', value: '1' }
  { name: 'INSPRO_DB_POOL_SIZE', value: string(reviewWorkerDbPoolSize) }
  { name: 'INSPRO_DB_MAX_OVERFLOW', value: string(reviewWorkerDbMaxOverflow) }
  { name: 'INSPRO_REVIEW_WORKER_CONCURRENCY', value: string(reviewWorkerConcurrency) }
  { name: 'INSPRO_REVIEW_MAX_CONCURRENT_PER_CLIENT', value: string(reviewWorkerMaxConcurrentPerClient) }
  { name: 'OTEL_SERVICE_NAME', value: '${prefix}-claim-review-worker' }
])

resource reviewWorker 'Microsoft.Web/sites@2024-04-01' = {
  name: '${appName}-review-worker'
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    virtualNetworkSubnetId: enableVnetIntegration ? privateNetworking.outputs.appSubnetId : null
    siteConfig: {
      linuxFxVersion: 'DOCKER|${containerImage}'
      appCommandLine: 'python -m app.workers.claim_review'
      acrUseManagedIdentityCreds: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/readyz'
      alwaysOn: true
      appSettings: workerAppSettings
    }
  }
  dependsOn: deployRedis ? [kvSecretRedisUrl] : []
}

// NOTE: there is deliberately no deployment slot.
// Deployment slots require Standard tier or higher — Basic offers zero. This
// deployment runs on Basic (B1 staging / B2 prod), so prod releases are
// in-place with a short restart, which is an accepted trade-off. Rollback is
// redeploying the previous image tag rather than swapping a slot. Reintroduce
// the slot (and the swap steps in deploy.yml) if the plan moves to S1/P0v3+.

// Grant the App Service managed identity Key Vault Secrets User role —
// required for runtime resolution of @Microsoft.KeyVault(...) references.
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, webapp.id, 'kv-secrets-user')
  properties: {
    principalId: webapp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

resource workerKvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, reviewWorker.id, 'kv-secrets-user')
  properties: {
    principalId: reviewWorker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

// Blob access for retained documents — managed identity, no account keys.
resource storageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, webapp.id, 'blob-contributor')
  properties: {
    principalId: webapp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

resource workerStorageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, reviewWorker.id, 'blob-contributor')
  properties: {
    principalId: reviewWorker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

// ACR pull permission, granted ON THE REGISTRY in the shared resource group.
// Previously this was an inline assignment with no `scope`, so it defaulted to
// this environment's resource group — which holds no registry. The webapp
// therefore had no pull rights and the container never started.
module acrPullGrant 'modules/acr-pull.bicep' = {
  name: 'acr-pull-${env}'
  scope: resourceGroup(acrResourceGroup)
  params: {
    acrName: acrName
    principalId: webapp.identity.principalId
    roleNameSeed: appName
  }
}

module workerAcrPullGrant 'modules/acr-pull.bicep' = {
  name: 'acr-pull-worker-${env}'
  scope: resourceGroup(acrResourceGroup)
  params: {
    acrName: acrName
    principalId: reviewWorker.identity.principalId
    roleNameSeed: '${appName}-review-worker'
  }
}

// ── Diagnostic settings — ship logs/metrics to Log Analytics ────────────────
resource webappDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: webapp
  name: 'to-law'
  properties: {
    workspaceId: law.id
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource reviewWorkerDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: reviewWorker
  name: 'to-law'
  properties: {
    workspaceId: law.id
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource postgresDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: postgres
  name: 'to-law'
  properties: {
    workspaceId: law.id
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

resource kvDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: kv
  name: 'to-law'
  properties: {
    workspaceId: law.id
    logs: [
      { categoryGroup: 'allLogs', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}

// ── Alerts ──────────────────────────────────────────────────────────────────
resource alertActionGroup 'Microsoft.Insights/actionGroups@2024-10-01-preview' = if (!empty(alertEmail)) {
  name: '${prefix}-ag'
  location: 'global'
  properties: {
    groupShortName: take('${env}alerts', 12)
    enabled: true
    emailReceivers: [
      { name: 'ops', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

resource http5xxAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(alertEmail)) {
  name: '${prefix}-http5xx-alert'
  location: 'global'
  properties: {
    severity: 2
    enabled: true
    scopes: [ webapp.id ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'Http5xx'
          metricName: 'Http5xx'
          metricNamespace: 'Microsoft.Web/sites'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: !empty(alertEmail) ? [
      { actionGroupId: alertActionGroup.id }
    ] : []
  }
}

resource reviewWorkerHealthAlert 'Microsoft.Insights/metricAlerts@2018-03-01' = if (!empty(alertEmail)) {
  name: '${prefix}-review-worker-health-alert'
  location: 'global'
  properties: {
    severity: 1
    enabled: true
    scopes: [reviewWorker.id]
    evaluationFrequency: 'PT1M'
    windowSize: 'PT5M'
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'WorkerHealthCheck'
          metricName: 'HealthCheckStatus'
          metricNamespace: 'Microsoft.Web/sites'
          operator: 'LessThan'
          threshold: 100
          timeAggregation: 'Average'
          criterionType: 'StaticThresholdCriterion'
        }
      ]
    }
    actions: [
      { actionGroupId: alertActionGroup.id }
    ]
  }
}

resource reviewQueueAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (!empty(alertEmail)) {
  name: '${prefix}-review-queue-alert'
  location: location
  properties: {
    displayName: 'Claim review queue age exceeded'
    description: 'The oldest available claim-review job exceeded the configured queue-age threshold.'
    severity: 1
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | where tostring(customDimensions.error_code) == "queue_age_exceeded"
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [alertActionGroup.id]
    }
  }
}

resource reviewFailureAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = if (!empty(alertEmail)) {
  name: '${prefix}-review-failure-alert'
  location: location
  properties: {
    displayName: 'Claim review operational failure'
    description: 'A lease, provider, terminal-job, or state-invariant failure requires attention.'
    severity: 1
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT10M'
    criteria: {
      allOf: [
        {
          query: '''
            traces
            | extend code = tostring(customDimensions.error_code)
            | where code == "lease_expired"
                or code startswith "ai_configuration"
                or code in ("pending_without_job", "active_missing_record")
                or message == "Claim-review job terminated"
          '''
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [alertActionGroup.id]
    }
  }
}

output appServiceUrl string = 'https://${webapp.properties.defaultHostName}'
output reviewWorkerUrl string = 'https://${reviewWorker.properties.defaultHostName}'
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output redisHost string = deployRedis ? '${redis!.name}.${location}.redis.azure.net' : ''
output keyVaultName string = kv.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
