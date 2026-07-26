#!/usr/bin/env bash
# Point the Postgres firewall at exactly this app's outbound IPs.
#
# Replaces the old `AllowAzureServices` (0.0.0.0) rule, which opened the
# database holding members' health and claims data to every Azure customer's
# outbound range.
#
# Runs as ONE ARM deployment of modules/db-firewall.bicep rather than a loop of
# `az ... firewall-rule create` calls: an App Service reports ~29 possible
# outbound IPs and each CLI call is a separate ~30s server update, so the loop
# took ~15 minutes and reconfigured the server 29 times.
#
# Idempotent, and safe to re-run after any App Service tier change — that
# re-issues the outbound IPs and would otherwise lock the app out of its
# database. `--mode Incremental` (the default) leaves unrelated rules alone, so
# stale `app-*` rules are pruned explicitly below.
#
# Usage: sync-db-firewall.sh <resource-group> <webapp-name> <postgres-name>
set -euo pipefail

RG="${1:?resource group required}"
APP="${2:?webapp name required}"
PG="${3:?postgres server name required}"
MODULE="$(dirname "$0")/../bicep/modules/db-firewall.bicep"

echo "Resolving outbound IPs for ${APP}…"
# `possibleOutboundIpAddresses` (not `outboundIpAddresses`) is the complete set
# the app may egress from; the shorter list changes on restart and would leave
# the app intermittently locked out.
IPS=$(az webapp show --name "$APP" --resource-group "$RG" \
  --query possibleOutboundIpAddresses -o tsv | tr ',' '\n' | tr -d ' \r' | sort -u | grep -v '^$')

if [ -z "$IPS" ]; then
  echo "ERROR: no outbound IPs returned for ${APP}" >&2
  exit 1
fi

COUNT=$(echo "$IPS" | wc -l | tr -d ' ')
JSON=$(echo "$IPS" | awk 'BEGIN{printf "["} {printf "%s\"%s\"", (NR>1 ? "," : ""), $0} END{printf "]"}')
echo "Applying ${COUNT} firewall rule(s) to ${PG} in a single deployment…"

az deployment group create \
  --resource-group "$RG" \
  --name "db-firewall-$(echo "$APP" | tr -cd '[:alnum:]-')" \
  --template-file "$MODULE" \
  --parameters postgresName="$PG" allowedIps="$JSON" \
  --query "properties.provisioningState" -o tsv

# Prune rules from a previous, larger IP set — Incremental mode won't remove
# them, so a scale-down would otherwise leave addresses allowed that this app
# no longer uses.
DESIRED=$(echo "$IPS" | sed 's/\./-/g' | sed 's/^/app-/')
# `tr -d '\r'`: az emits CRLF on Windows, and a trailing \r both breaks the
# exact-match below (so a live rule looks stale) and makes the delete call fail
# validation — which, under `set -e`, aborted the prune entirely.
EXISTING=$(az postgres flexible-server firewall-rule list \
  --resource-group "$RG" --name "$PG" \
  --query "[?starts_with(name, 'app-')].name" -o tsv 2>/dev/null | tr -d '\r' || true)

for name in $EXISTING; do
  if ! echo "$DESIRED" | grep -qx "$name"; then
    echo "Pruning stale rule ${name}"
    # `|| true`: one bad rule name must not abort the whole prune.
    az postgres flexible-server firewall-rule delete \
      --resource-group "$RG" --name "$PG" --rule-name "$name" \
      --yes --only-show-errors >/dev/null || true
  fi
done

# Remove the legacy blanket rule if an earlier deployment created it.
if az postgres flexible-server firewall-rule show \
     --resource-group "$RG" --name "$PG" --rule-name AllowAzureServices \
     --only-show-errors >/dev/null 2>&1; then
  echo "Removing legacy AllowAzureServices (0.0.0.0) rule"
  az postgres flexible-server firewall-rule delete \
    --resource-group "$RG" --name "$PG" --rule-name AllowAzureServices \
    --yes --only-show-errors >/dev/null
fi

echo "Firewall sync complete."
