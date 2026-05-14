#!/usr/bin/env bash
# Poll for the Anthropic Marketplace subscription becoming effective on the
# new Azure account, then auto-deploy claude-opus-4-7 / claude-sonnet-4-6 /
# claude-haiku-4-5 the moment the gate clears.
#
# Why this exists: the modelProviderData gate is set server-side by clicking
# "Subscribe to model" once in Azure AI Studio. Until that click happens, all
# az cognitiveservices deployments of Anthropic models 400 with
#   InvalidModelProviderData
# After the click, identical CLI calls succeed. The script polls a cheap probe
# deployment (deletes immediately on success), so the user can stay on the
# Azure AI Studio Subscribe page and we react instantly when it clicks through.
#
# Usage:
#   bash scripts/await-anthropic-subscribe.sh
#
# Required env:
#   az login as mike@southwestmushrooms.com (Azure subscription 1).
#
# Exits 0 after all 3 Claude deployments succeed.

set -euo pipefail

CS_ACCT="crowelm-prod-eastus2"
CS_RG="rg-crowelm-prod"
POLL_INTERVAL="${POLL_INTERVAL:-20}"  # seconds between probes
POLL_MAX="${POLL_MAX:-180}"           # ~1 hour max wall time

log() { printf "\n\033[1;34m[%s] %s\033[0m\n" "$(date +%H:%M:%S)" "$*"; }
ok()  { printf "  \033[1;32mOK %s\033[0m\n" "$*"; }
warn(){ printf "  \033[1;33m.. %s\033[0m\n" "$*"; }
err() { printf "  \033[1;31mFAIL %s\033[0m\n" "$*"; }

probe_subscribe_gate() {
  # Tries to create a tiny probe deployment to detect the gate state.
  # Returns:
  #   0 = gate is open (subscription is effective)
  #   1 = gate is closed (InvalidModelProviderData)
  #   2 = some other error
  local probe_name="anthropic-gate-probe-$$"
  local result
  result=$(az cognitiveservices account deployment create \
    --name "$CS_ACCT" --resource-group "$CS_RG" \
    --deployment-name "$probe_name" \
    --model-name "claude-opus-4-7" --model-version "1" \
    --model-format "Anthropic" \
    --sku-name "GlobalStandard" --sku-capacity "1" \
    -o tsv --query "properties.provisioningState" 2>&1) || true

  if [[ "$result" == "Succeeded" ]]; then
    # Gate is open. Tear down the probe immediately.
    az cognitiveservices account deployment delete \
      --name "$CS_ACCT" --resource-group "$CS_RG" \
      --deployment-name "$probe_name" --yes >/dev/null 2>&1 || true
    return 0
  elif echo "$result" | grep -q "InvalidModelProviderData"; then
    return 1
  else
    echo "$result" | head -3
    return 2
  fi
}

deploy_claude() {
  local name=$1 model=$2 version=${3:-1}
  if az cognitiveservices account deployment show \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" >/dev/null 2>&1; then
    ok "$name already deployed"
    return 0
  fi
  if az cognitiveservices account deployment create \
       --name "$CS_ACCT" --resource-group "$CS_RG" \
       --deployment-name "$name" \
       --model-name "$model" --model-version "$version" \
       --model-format "Anthropic" \
       --sku-name "GlobalStandard" --sku-capacity "1" \
       --query "properties.provisioningState" -o tsv 2>err.log; then
    ok "$name deployed (Anthropic $model:$version)"
    rm -f err.log
    return 0
  else
    err "$name failed: $(tail -2 err.log | tr '\n' ' ')"
    rm -f err.log
    return 1
  fi
}

log "Polling Anthropic Marketplace gate on $CS_ACCT every ${POLL_INTERVAL}s"
log "Subscribe URL: https://ai.azure.com/explore/models/claude-opus-4-7/version/1/registry/azureml-anthropic"

attempt=0
while (( attempt < POLL_MAX )); do
  attempt=$((attempt + 1))
  if probe_subscribe_gate; then
    ok "Gate is OPEN. Subscribe took effect after attempt $attempt."
    break
  else
    warn "attempt $attempt: gate still closed (InvalidModelProviderData)"
    sleep "$POLL_INTERVAL"
  fi
done

if (( attempt >= POLL_MAX )); then
  err "Gave up after $((POLL_MAX * POLL_INTERVAL))s. Re-run after clicking Subscribe in Azure AI Studio."
  exit 1
fi

log "Deploying 3 Claude variants in priority order"
FAILED=0
deploy_claude claude-opus-4-7   claude-opus-4-7   1 || FAILED=$((FAILED+1))
deploy_claude claude-sonnet-4-6 claude-sonnet-4-6 1 || FAILED=$((FAILED+1))
deploy_claude claude-haiku-4-5  claude-haiku-4-5  1 || FAILED=$((FAILED+1))

if (( FAILED == 0 )); then
  log "All 3 Anthropic deployments live on $CS_ACCT. Quota requests filed yesterday will now bind to real deployments."
  log "Next step: bump capacity in-place via az cognitiveservices account deployment update --sku-capacity <N> once Microsoft grants quota."
else
  err "$FAILED of 3 deployments failed. Re-run scripts/deploy-azure-frontier.sh to retry."
  exit 1
fi
