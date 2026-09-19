#!/usr/bin/env bash
set -euo pipefail

RG="${1:?resource group required}"
JOB="${2:?job name required}"
IMAGE="${3:?immutable image required}"
SHA="${4:?commit SHA required}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ && "$IMAGE" == *":$SHA" ]] || { echo 'Invalid release image'; exit 1; }
az extension add --name containerapp --upgrade --only-show-errors
RUNNING=$(az containerapp job execution list -g "$RG" -n "$JOB" --query "[?properties.status=='Running'].name" -o tsv)
[ -z "$RUNNING" ] || { echo 'Another migration execution is running; refusing overlap.'; exit 1; }
az containerapp job update -g "$RG" -n "$JOB" --image "$IMAGE" \
  --set-env-vars "INSPRO_EXPECTED_GIT_SHA=$SHA" --only-show-errors -o none
EXECUTION=$(az containerapp job start -g "$RG" -n "$JOB" --query name -o tsv)
[[ "$EXECUTION" == "$JOB-"* ]] || { echo 'Missing migration execution identity'; exit 1; }
echo "Migration execution: $EXECUTION"
for _ in $(seq 1 100); do
  STATUS=$(az containerapp job execution show -g "$RG" -n "$JOB" \
    --job-execution-name "$EXECUTION" --query properties.status -o tsv)
  case "$STATUS" in
    Succeeded) echo 'Private migrations succeeded.'; exit 0 ;;
    Failed|Stopped|Degraded) echo "Private migrations ended with $STATUS; deployment blocked."; exit 1 ;;
  esac
  sleep 10
done
echo 'Timed out waiting for private migrations; deployment blocked. Inspect the execution before retrying.'
exit 1
