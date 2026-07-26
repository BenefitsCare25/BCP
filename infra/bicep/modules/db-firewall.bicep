// Postgres firewall rules for an App Service's outbound IPs.
//
// Deployed SEPARATELY from main.bicep because the IP list only exists once the
// webapp does, and Bicep requires loop counts to resolve at the start of a
// deployment (BCP178) — so the list arrives here as a parameter instead of a
// runtime reference.
//
// One ARM deployment, not one CLI call per rule: an App Service reports ~29
// possible outbound IPs, and every `az postgres flexible-server firewall-rule
// create` is its own server update taking ~30s. Serially that is ~15 minutes of
// repeated server reconfiguration; declaring them together applies the whole
// set in a single update.

targetScope = 'resourceGroup'

@description('Postgres Flexible Server name.')
param postgresName string

@description('Outbound IPv4 addresses allowed to reach the database.')
param allowedIps array

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: postgresName
}

// Rule names are derived from the IP, not the index, so re-running with a
// reordered list doesn't rewrite every rule.
resource rules 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = [
  for ip in allowedIps: {
    parent: postgres
    name: 'app-${replace(trim(ip), '.', '-')}'
    properties: {
      startIpAddress: trim(ip)
      endIpAddress: trim(ip)
    }
  }
]

output ruleCount int = length(allowedIps)
