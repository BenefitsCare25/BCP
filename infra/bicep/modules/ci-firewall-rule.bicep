// A single, temporary Postgres firewall rule for the CI runner.
//
// Migrations run from a GitHub-hosted runner rather than from inside the VNet,
// so the runner needs a momentary hole in the otherwise-empty public allowlist.
// deploy.yml removes it again in an `always()` step.
//
// This is an ARM deployment rather than `az postgres flexible-server
// firewall-rule create` on purpose. That command's `--name` flag changed meaning
// between Azure CLI versions — it used to be the SERVER name and is now the RULE
// name, with a separate required `--server-name`. The workflow was written
// against the old signature, and when the GitHub runner image picked up the
// newer CLI every prod deploy started failing with
// "the following arguments are required: --server-name/-s", skipping migrations
// and the image deploy. ARM has no such ambiguity, and `az deployment group
// create` polls to completion so the rule is live before Alembic connects.

targetScope = 'resourceGroup'

@description('Postgres Flexible Server to open.')
param postgresName string

@description('Firewall rule name, e.g. ci-runner-<run-id>.')
param ruleName string

@description('Single IPv4 address to admit.')
param allowedIp string

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' existing = {
  name: postgresName
}

resource rule 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: ruleName
  properties: {
    startIpAddress: allowedIp
    endIpAddress: allowedIp
  }
}
