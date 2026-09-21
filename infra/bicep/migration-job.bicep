// Provision separately from the live application stack. No database/public firewall changes.
// After provisioning, add output subnetId with `az keyvault network-rule add`.
// Preserve the vault's other ACLs; do not redeploy the live application's main stack.
targetScope = 'resourceGroup'

param location string = 'southeastasia'
param prefix string = 'inspro-prod'
param productionVnetName string = 'inspro-prod-vnet'
param vaultName string = 'inspro-prod-kv'
param registryName string = 'insproacr'
param registryResourceGroup string = 'rg-inspro-prod'
param workspaceName string = 'inspro-prod-law'
@description('Existing immutable release image. Creating the manual job does not execute it.')
param image string

resource productionVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = { name: productionVnetName }
resource postgresDns 'Microsoft.Network/privateDnsZones@2020-06-01' existing = {
  name: 'privatelink.postgres.database.azure.com'
}
resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: vaultName }
resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = { name: workspaceName }

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-migration-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.21.0.0/24'] }
    subnets: [{
      name: 'jobs'
      properties: {
        addressPrefix: '10.21.0.0/27'
        delegations: [{ name: 'containerapps', properties: { serviceName: 'Microsoft.App/environments' } }]
        serviceEndpoints: [{ service: 'Microsoft.KeyVault', locations: ['*'] }]
      }
    }]
  }
}
resource toProduction 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: network
  name: 'production'
  properties: {
    remoteVirtualNetwork: { id: productionVnet.id }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}
resource fromProduction 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: productionVnet
  name: 'migrations'
  properties: {
    remoteVirtualNetwork: { id: network.id }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}
resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: postgresDns
  name: '${prefix}-migration-link'
  location: 'global'
  properties: { virtualNetwork: { id: network.id }, registrationEnabled: false }
}
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-migration-identity'
  location: location
}
// Preserve the existing tenant provisioning/reconciliation command, which needs
// the AI encryption key for legacy configuration validation, but no portal/SMTP secrets.
resource secrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = [for name in ['database-url', 'ai-key-encryption-key']: {
  parent: vault
  name: name
}]
resource secretAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in ['database-url', 'ai-key-encryption-key']: {
  scope: secrets[i]
  name: guid(secrets[i].id, identity.id, 'migration-secret-reader')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
  }
}]
module pull 'modules/acr-pull.bicep' = {
  name: '${prefix}-migration-acr'
  scope: resourceGroup(registryResourceGroup)
  params: { acrName: registryName, principalId: identity.properties.principalId, roleNameSeed: 'migration-pull' }
}
resource environment 'Microsoft.App/managedEnvironments@2025-07-01' = {
  name: '${prefix}-migration-env'
  location: location
  properties: {
    publicNetworkAccess: 'Disabled'
    vnetConfiguration: { infrastructureSubnetId: '${network.id}/subnets/jobs', internal: true }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: workspace.properties.customerId, sharedKey: workspace.listKeys().primarySharedKey }
    }
  }
}
resource job 'Microsoft.App/jobs@2025-07-01' = {
  name: '${prefix}-migrate'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 900
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: '${registryName}.azurecr.io', identity: identity.id }]
    }
    template: {
      containers: [{
        name: 'migrate'
        image: image
        command: ['python', '-m', 'scripts.run_private_migrations']
        resources: { cpu: 1, memory: '2Gi' }
        env: [
          { name: 'INSPRO_ENV', value: 'prod' }
          { name: 'INSPRO_MIGRATION_VAULT_URL', value: vault.properties.vaultUri }
          { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
          { name: 'INSPRO_MIGRATION_DATABASE_HOST', value: 'inspro-prod-pg-recovery-20260825.postgres.database.azure.com' }
          { name: 'INSPRO_MIGRATION_DATABASE_NETWORK', value: '10.20.0.0/16' }
          { name: 'INSPRO_DB_POOL_SIZE', value: '1' }
          { name: 'INSPRO_DB_MAX_OVERFLOW', value: '0' }
        ]
      }]
    }
  }
  dependsOn: [pull, secretAccess, dnsLink, toProduction, fromProduction]
}

output jobName string = job.name
output subnetId string = '${network.id}/subnets/jobs'
