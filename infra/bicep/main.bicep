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
// - App Service Plan + Linux Web App (container, with prod 'staging' slot)
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

@description('Enable geo-redundant Postgres backups. Recommend true for prod.')
param postgresGeoRedundantBackup bool = false

@description('Redis SKU.')
param redisSku string

@description('Redis size.')
param redisCapacity int

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

@description('ACR registry hostname for the webapp to pull from, e.g. insproacr.azurecr.io.')
param acrLoginServer string

@description('Notification email for HTTP 5xx alerts (optional).')
param alertEmail string = ''

@description('Portal OTP mail delivery mode. The app refuses to boot in prod with "log" (OTP codes would land in application logs in cleartext).')
@allowed(['log', 'smtp', 'acs'])
param mailMode string = 'smtp'

@description('SMTP host for portal OTP mail (mailMode=smtp). Sends fail visibly (mail_sent=false) until configured.')
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

// Built-in role IDs (https://learn.microsoft.com/azure/role-based-access-control/built-in-roles).
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
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
    enablePurgeProtection: isProd
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
      storageSizeGB: 32
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: postgresBackupRetentionDays
      geoRedundantBackup: postgresGeoRedundantBackup ? 'Enabled' : 'Disabled'
    }
    highAvailability: {
      mode: isProd ? 'ZoneRedundant' : 'Disabled'
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

// TODO(prod): replace the broad AllowAzureServices rule with VNet integration
// + private endpoint. AllowAzureServices opens the DB to any other Azure
// customer's outbound IP — it's how App Service reaches Postgres without a
// VNet but it's not defensible long-term.
resource postgresFwAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// ── Redis ────────────────────────────────────────────────────────────────────
resource redis 'Microsoft.Cache/redis@2024-11-01' = {
  name: '${prefix}-redis'
  location: location
  properties: {
    sku: {
      name: redisSku
      family: redisSku == 'Premium' ? 'P' : 'C'
      capacity: redisCapacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: {
      // Enable AOF persistence for prod so a Redis restart doesn't wipe the
      // AI response cache / SlowAPI counters. Premium-tier-only.
      'aof-backup-enabled': isProd && redisSku == 'Premium' ? 'true' : 'false'
    }
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

resource kvSecretRedisUrl 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'redis-url'
  properties: {
    value: 'rediss://:${redis.listKeys().primaryKey}@${redis.properties.hostName}:6380/0'
  }
}

resource kvSecretPortalJwt 'Microsoft.KeyVault/vaults/secrets@2024-04-01-preview' = {
  parent: kv
  name: 'portal-jwt-secret'
  properties: {
    value: portalJwtSecret
  }
}

// ── Retained document storage (claim receipts, dependant proofs — PII) ──────
// Private blob container; the app reads/writes via managed identity
// (INSPRO_STORAGE_MODE=azure + INSPRO_STORAGE_ACCOUNT_URL, no keys in config).
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: replace('${prefix}docs', '-', '')
  location: location
  sku: { name: isProd ? 'Standard_GRS' : 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
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
  { name: 'WEBSITES_PORT', value: '8000' }
  { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://${acrLoginServer}' }
  { name: 'INSPRO_ENV', value: env }
  { name: 'INSPRO_AUTH_MODE', value: 'entra' }
  { name: 'INSPRO_ENTRA_TENANT_ID', value: entraTenantId }
  { name: 'INSPRO_ENTRA_CLIENT_ID', value: entraClientId }
  { name: 'INSPRO_ENTRA_AUDIENCE', value: 'api://${entraClientId}' }
  { name: 'INSPRO_ENTRA_ISSUER', value: 'https://login.microsoftonline.com/${entraTenantId}/v2.0' }
  { name: 'INSPRO_ENTRA_JWKS_URL', value: 'https://login.microsoftonline.com/${entraTenantId}/discovery/v2.0/keys' }
  { name: 'INSPRO_CORS_ORIGINS', value: corsOrigins }
  { name: 'INSPRO_DATABASE_URL', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretDatabaseUrl.name})' }
  { name: 'INSPRO_REDIS_URL', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretRedisUrl.name})' }
  { name: 'INSPRO_PORTAL_JWT_SECRET', value: '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${kvSecretPortalJwt.name})' }
  // Portal OTP mail. Fail-closed: the app refuses to boot in prod on "log"
  // mode; with smtp unconfigured it boots and each send fails VISIBLY
  // (mail_sent=false on invite responses) instead of leaking codes to logs.
  { name: 'INSPRO_MAIL_MODE', value: mailMode }
  { name: 'INSPRO_SMTP_HOST', value: smtpHost }
  { name: 'INSPRO_SMTP_PORT', value: smtpPort }
  { name: 'INSPRO_SMTP_USER', value: smtpUser }
  { name: 'INSPRO_SMTP_FROM', value: smtpFrom }
  { name: 'INSPRO_SMTP_PASSWORD', value: smtpPassword }
  { name: 'INSPRO_STORAGE_MODE', value: 'azure' }
  { name: 'INSPRO_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'INSPRO_STORAGE_CONTAINER', value: documentsContainer.name }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
  // Auto-instrument FastAPI/SQLAlchemy via azure-monitor-opentelemetry distro.
  { name: 'OTEL_SERVICE_NAME', value: '${prefix}-api' }
]

// Shared site config — used by the primary webapp and (for prod) the
// 'staging' slot. Container-based deploy with ACR pull via managed identity.
var siteConfig = {
  linuxFxVersion: 'DOCKER|${containerImage}'
  acrUseManagedIdentityCreds: true
  ftpsState: 'Disabled'
  minTlsVersion: '1.2'
  http20Enabled: true
  healthCheckPath: '/health'
  appSettings: commonAppSettings
}

resource webapp 'Microsoft.Web/sites@2024-04-01' = {
  name: '${prefix}-api'
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: union(siteConfig, { alwaysOn: isProd })
  }
}

// Prod gets a 'staging' deployment slot so the workflow can `slot swap`.
// Staging env doesn't need a slot — re-deploys can blow away its single instance.
resource prodStagingSlot 'Microsoft.Web/sites/slots@2024-04-01' = if (isProd) {
  parent: webapp
  name: 'staging'
  location: location
  kind: 'app,linux,container'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: union(siteConfig, { alwaysOn: true })
  }
}

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

resource slotKvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (isProd) {
  scope: kv
  name: guid(kv.id, prodStagingSlot.id, 'kv-secrets-user-slot')
  properties: {
    principalId: prodStagingSlot.identity.principalId
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

resource slotStorageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (isProd) {
  scope: storage
  name: guid(storage.id, prodStagingSlot.id, 'blob-contributor-slot')
  properties: {
    principalId: prodStagingSlot.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

// ACR pull permission for the webapp's managed identity.
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Scope is left implicit (resource group level) — broader than ideal but the
  // ACR lives in rg-inspro-shared so a tighter scope requires a multi-RG deploy.
  name: guid(resourceGroup().id, webapp.id, 'acr-pull')
  properties: {
    principalId: webapp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
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

output appServiceUrl string = 'https://${webapp.properties.defaultHostName}'
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output redisHost string = redis.properties.hostName
output keyVaultName string = kv.name
output appInsightsConnectionString string = appInsights.properties.ConnectionString
