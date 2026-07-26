// Private network path from the App Service to Postgres.
//
// Replaces the per-IP firewall allowlist (infra/scripts/sync-db-firewall.sh),
// which enumerated the App Service's ~44 `possibleOutboundIpAddresses` as
// individual firewall rules. That design had three problems:
//   1. Azure Postgres applies each firewall rule as its own server update, so a
//      full sync took ~20 minutes and reconfigured the live database.
//   2. The IPs are re-issued whenever the App Service plan tier changes, which
//      silently locks the app out of its database until the sync re-runs.
//   3. They are SHARED App Service infrastructure addresses, not addresses we
//      own — so allowlisting them also admits other tenants on the same scale
//      unit.
//
// Instead the app reaches Postgres over a private endpoint: a NIC inside our own
// VNet with a private IP, resolved through a private DNS zone. Nothing traverses
// the public internet and no IP list needs maintaining.
//
// Deliberately NOT enabling `vnetRouteAllEnabled` on the web app (see
// main.bicep). Only RFC1918 traffic bound for this VNet is routed through the
// integration subnet; everything else (Vertex AI, Entra JWKS, SMTP, ACR, Key
// Vault, Blob) keeps using the normal App Service egress path. Routing all
// outbound traffic through the VNet would require a NAT Gateway, because Azure
// has retired default outbound access for new VNet deployments.

targetScope = 'resourceGroup'

@description('Resource name prefix, e.g. inspro-prod.')
param prefix string

@description('Azure region. Must match the app and the database — regional VNet integration requires all three in the same region.')
param location string

@description('Existing Postgres Flexible Server to place behind a private endpoint.')
param postgresName string

@description('VNet address space. 10.0.0.0/16 and 10.1.0.0/16 are already taken by rg-ivm and rg-supabase-sea in this subscription.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Subnet delegated to App Service for regional VNet integration. Microsoft recommends at least a /26 — App Service consumes addresses per instance during scale and slot swaps.')
param appSubnetPrefix string = '10.20.1.0/24'

@description('Subnet holding the Postgres private endpoint NIC.')
param privateEndpointSubnetPrefix string = '10.20.2.0/24'

// The zone name is not a free choice — Azure returns it as `requiredZoneNames`
// on the server's privateLinkResources, and the private endpoint's DNS zone
// group only writes an A record into a zone with exactly this name.
var privateDnsZoneName = 'privatelink.postgres.database.azure.com'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: postgresName
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-vnet'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [ vnetAddressPrefix ]
    }
    // Subnets are declared inline rather than as child resources. Azure
    // serialises writes to a VNet, and separate child-resource deployments of
    // two subnets race and intermittently fail with AnotherOperationInProgress.
    //
    // The trade-off: this array is AUTHORITATIVE. A subnet added to this VNet
    // out-of-band (portal, CLI) is deleted by the next deployment, even in
    // Incremental mode. Add new subnets here, never in the portal.
    subnets: [
      {
        name: 'snet-app'
        properties: {
          addressPrefix: appSubnetPrefix
          // Regional VNet integration requires the subnet be delegated to App
          // Service and used by nothing else.
          delegations: [
            {
              name: 'appservice'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          // Private endpoints cannot be created in a subnet that enforces
          // network policies on them.
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource privateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: privateDnsZoneName
  // Private DNS zones are global resources; 'global' is the only valid value.
  location: 'global'
}

// Without this link the zone exists but the VNet never consults it, so the app
// would still resolve the database's PUBLIC IP and fail against an empty
// firewall allowlist.
resource privateDnsZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: privateDnsZone
  name: '${prefix}-vnet-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    // No auto-registration: this zone holds one manually-managed private
    // endpoint record, not VM hostnames.
    registrationEnabled: false
  }
}

resource postgresPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${prefix}-pg-pe'
  location: location
  properties: {
    subnet: {
      id: '${vnet.id}/subnets/snet-pe'
    }
    privateLinkServiceConnections: [
      {
        name: 'postgres'
        properties: {
          privateLinkServiceId: postgres.id
          // Fixed by Azure — `az rest .../privateLinkResources` reports exactly
          // this one group for a Flexible Server.
          groupIds: [ 'postgresqlServer' ]
        }
      }
    ]
  }
}

// Writes the A record (inspro-prod-pg.privatelink.postgres.database.azure.com ->
// the endpoint's private IP) automatically, and removes it if the endpoint is
// deleted. Doing this by hand leaves a stale record pointing at a dead NIC.
resource privateDnsZoneGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: postgresPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'postgres'
        properties: {
          privateDnsZoneId: privateDnsZone.id
        }
      }
    ]
  }
}

@description('Subnet the web app integrates with. Consumed by main.bicep as virtualNetworkSubnetId.')
output appSubnetId string = '${vnet.id}/subnets/snet-app'

// Deliberately NOT emitting the endpoint's private IP. `customDnsConfigs` is
// populated only while the endpoint is being created — every later read returns
// `[]`, so an output indexing into it succeeds on the first deployment and then
// fails every subsequent one with an index-out-of-bounds. To check the address,
// read the A record the zone group wrote:
//   az network private-dns record-set a list -g <rg> \
//     -z privatelink.postgres.database.azure.com
output vnetId string = vnet.id
