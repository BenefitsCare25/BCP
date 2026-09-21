// Grant AcrPull directly on the registry to an app's managed identity. The
// module remains resource-group scoped so a controlled registry move does not
// weaken the role assignment to the whole production resource group.

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
