// Grant AcrPull on the SHARED registry to an app's managed identity.
//
// This exists as a module because the grant must be scoped to the ACR, which
// lives in a different resource group (rg-inspro-shared) from the environment
// being deployed. The previous inline role assignment in main.bicep declared no
// `scope`, so it landed on the environment's own resource group — which
// contains no registry. With `acrUseManagedIdentityCreds: true` the webapp then
// had no permission to pull its own image and never started.

targetScope = 'resourceGroup'

@description('Name of the container registry in THIS resource group.')
param acrName string

@description('Principal ID of the managed identity that needs pull access.')
param principalId string

@description('Stable discriminator so multiple apps can each hold a grant.')
param roleNameSeed string

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, principalId, roleNameSeed)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      acrPullRoleId
    )
  }
}
