#!/usr/bin/env bash
# One-time cleanup after the move to a private endpoint.
#
# The retired sync-db-firewall.sh created one `app-<ip>` firewall rule per App
# Service outbound IP (~44 of them). Once the app reaches Postgres through the
# private endpoint those rules are dead weight — and worse than dead weight:
# they are SHARED App Service infrastructure addresses, not addresses we own, so
# leaving them allowlisted also admits other tenants on the same scale unit.
#
# Deleting them is slow for the same reason creating them was: Azure applies
# each firewall-rule change as its own server update. Expect ~20 minutes. It is
# safe to run while the app serves traffic — the app is not using these rules any
# more — and safe to interrupt and re-run, since it only ever deletes rules that
# still exist.
#
# Uses `az rest` rather than `az postgres flexible-server firewall-rule delete`
# because that command's `--name` flag changed meaning between CLI versions
# (server name -> rule name); `az rest` has no such ambiguity.
#
# Usage: prune-app-firewall-rules.sh <subscription-id> <resource-group> <postgres-name>
set -euo pipefail

SUB="${1:?subscription id required}"
RG="${2:?resource group required}"
PG="${3:?postgres server name required}"
API="2024-08-01"
BASE="https://management.azure.com/subscriptions/${SUB}/resourceGroups/${RG}/providers/Microsoft.DBforPostgreSQL/flexibleServers/${PG}/firewallRules"

# Guard: refuse to strip the allowlist unless the private path actually exists.
# Running this before the private endpoint is live and approved would cut the
# app off from its database with no route back.
PE_STATE=$(az network private-endpoint list --resource-group "$RG" \
  --query "[?contains(to_string(privateLinkServiceConnections[0].privateLinkServiceId), '${PG}')] | [0].privateLinkServiceConnections[0].privateLinkServiceConnectionState.status" \
  -o tsv 2>/dev/null | tr -d '\r' || true)

if [ "$PE_STATE" != "Approved" ]; then
  echo "ERROR: no Approved private endpoint found for ${PG} (state: '${PE_STATE:-none}')." >&2
  echo "Deploy infra/bicep/modules/private-networking.bicep and confirm the app is" >&2
  echo "healthy on the private path BEFORE pruning the public allowlist." >&2
  exit 1
fi

mapfile -t RULES < <(az rest --method get --url "${BASE}?api-version=${API}" \
  --query "value[?starts_with(name, 'app-')].name" -o tsv | tr -d '\r' | grep -v '^$' || true)

if [ "${#RULES[@]}" -eq 0 ]; then
  echo "No app-* firewall rules left on ${PG} — nothing to do."
  exit 0
fi

echo "Pruning ${#RULES[@]} obsolete app-* rule(s) from ${PG}."
echo "Azure applies each as its own server update, so this takes roughly $(( ${#RULES[@]} / 2 )) minutes."

i=0
for name in "${RULES[@]}"; do
  i=$((i + 1))
  printf '[%d/%d] %s… ' "$i" "${#RULES[@]}" "$name"
  # Sequential, not parallel: concurrent server updates collide with
  # AnotherOperationInProgress and the whole run would need retrying.
  if az rest --method delete --url "${BASE}/${name}?api-version=${API}" --only-show-errors >/dev/null 2>&1; then
    echo "deleted"
  else
    # Keep going — one stuck rule must not strand the other 43.
    echo "FAILED (re-run to retry)"
  fi
done

# A firewall-rule DELETE returns 202 Accepted and Azure drains it in the
# background, so counting straight after the loop reports rules that are already
# on their way out — the first run of this script printed "remaining: 21" when
# every one of the 44 deletes had in fact succeeded. Poll until the count settles
# instead of reporting a number that is guaranteed to be stale.
echo "Deletes accepted. Waiting for Azure to drain them…"
REMAINING=""
for _ in $(seq 1 40); do
  REMAINING=$(az rest --method get --url "${BASE}?api-version=${API}" \
    --query "length(value[?starts_with(name, 'app-')])" -o tsv | tr -d '\r')
  [ "$REMAINING" = "0" ] && break
  sleep 15
done

if [ "$REMAINING" = "0" ]; then
  echo "Done. All app-* rules removed."
else
  echo "WARNING: ${REMAINING} app-* rule(s) still present after 10 minutes." >&2
  echo "Re-run this script — it only deletes what still exists." >&2
  exit 1
fi
